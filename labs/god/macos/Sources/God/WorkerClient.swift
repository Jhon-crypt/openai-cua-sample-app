import Foundation

enum WorkerClientError: LocalizedError {
    case notRunning
    case spawnFailed(String)
    case timeout

    var errorDescription: String? {
        switch self {
        case .notRunning: return "Worker is not running."
        case .spawnFailed(let message): return message
        case .timeout: return "Timed out waiting for the worker."
        }
    }
}

@MainActor
final class WorkerClient: ObservableObject {
    @Published private(set) var statusText = "Disconnected"
    @Published private(set) var ready = false

    private var process: Process?
    private var stdinPipe: Pipe?
    private var readTask: Task<Void, Never>?
    private var stderrTask: Task<Void, Never>?
    private var pending: [String: CheckedContinuation<[String: Any], Error>] = [:]
    private var readyContinuation: CheckedContinuation<Void, Error>?
    private var buffer = Data()
    private var stderrText = ""
    private var sawReady = false

    func start() async throws {
        if process?.isRunning == true, ready { return }
        stopWorker()

        let workerProject = Self.workerProjectURL()
        guard FileManager.default.fileExists(atPath: workerProject.appendingPathComponent("pyproject.toml").path) else {
            throw WorkerClientError.spawnFailed("Worker project missing at \(workerProject.path)")
        }

        let uv = Self.resolveUvURL()
        let process = Process()
        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()

        process.executableURL = uv
        process.arguments = ["run", "--project", workerProject.path, "python", "-m", "god_worker"]
        process.environment = Self.workerEnvironment()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        process.terminationHandler = { [weak self] proc in
            Task { @MainActor in self?.handleTermination(exitCode: proc.terminationStatus) }
        }

        sawReady = false
        stderrText = ""
        readyContinuation = nil

        // Register waiter before run() so an early "ready" line cannot be missed.
        let readyWait = Task { @MainActor in
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if self.sawReady {
                    continuation.resume()
                    return
                }
                self.readyContinuation = continuation
            }
        }

        let timeout = Task { @MainActor in
            try await Task.sleep(nanoseconds: 60_000_000_000)
            if let pendingReady = self.readyContinuation {
                self.readyContinuation = nil
                let detail = self.stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
                let message = detail.isEmpty ? "Timed out waiting for the worker." : detail
                pendingReady.resume(throwing: WorkerClientError.spawnFailed(message))
            }
        }

        do {
            try process.run()
        } catch {
            timeout.cancel()
            readyWait.cancel()
            throw WorkerClientError.spawnFailed(error.localizedDescription)
        }

        self.process = process
        self.stdinPipe = stdin
        self.statusText = "Starting worker…"
        self.ready = false

        readTask = Task { await self.readLoop(handle: stdout.fileHandleForReading) }
        stderrTask = Task { await self.readStderr(handle: stderr.fileHandleForReading) }

        do {
            try await readyWait.value
            timeout.cancel()
        } catch {
            timeout.cancel()
            stopWorker()
            throw error
        }
    }

    func stopWorker() {
        let running = process
        if running?.isRunning == true {
            if let stdin = stdinPipe {
                let payload: [String: Any] = ["type": "stop", "id": "shutdown"]
                if let data = try? JSONSerialization.data(withJSONObject: payload) {
                    var line = data
                    line.append(0x0A)
                    try? stdin.fileHandleForWriting.write(contentsOf: line)
                    Thread.sleep(forTimeInterval: 0.15)
                }
            }
            running?.terminate()
        }
        readTask?.cancel()
        stderrTask?.cancel()
        handleTermination(exitCode: running?.terminationStatus)
    }

    func send(type: String) async throws -> [String: Any] {
        guard let stdin = stdinPipe, process?.isRunning == true, ready else {
            throw WorkerClientError.notRunning
        }
        let id = UUID().uuidString
        let payload: [String: Any] = ["type": type, "id": id]
        let data = try JSONSerialization.data(withJSONObject: payload)
        var line = data
        line.append(0x0A)

        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try stdin.fileHandleForWriting.write(contentsOf: line)
            } catch {
                pending.removeValue(forKey: id)
                continuation.resume(throwing: error)
            }
        }
    }

    private func handleTermination(exitCode: Int32?) {
        let wasWaiting = readyContinuation != nil
        ready = false
        process = nil
        stdinPipe = nil

        if let readyContinuation {
            self.readyContinuation = nil
            let detail = stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
            let code = exitCode.map(String.init) ?? "?"
            let message = detail.isEmpty
                ? "Worker exited before ready (code \(code)). Is uv installed?"
                : "Worker exited (code \(code)): \(detail)"
            statusText = message
            readyContinuation.resume(throwing: WorkerClientError.spawnFailed(message))
        } else if !wasWaiting {
            statusText = "Disconnected"
        }

        for (_, continuation) in pending {
            continuation.resume(throwing: WorkerClientError.notRunning)
        }
        pending.removeAll()
    }

    private func readLoop(handle: FileHandle) async {
        while !Task.isCancelled {
            let chunk = handle.availableData
            if chunk.isEmpty {
                if process?.isRunning != true { break }
                try? await Task.sleep(nanoseconds: 20_000_000)
                continue
            }
            buffer.append(chunk)
            while let newline = buffer.firstIndex(of: 0x0A) {
                let lineData = Data(buffer[..<newline])
                buffer.removeSubrange(...newline)
                handleLine(lineData)
            }
        }
    }

    private func readStderr(handle: FileHandle) async {
        while !Task.isCancelled {
            let chunk = handle.availableData
            if chunk.isEmpty {
                if process?.isRunning != true { break }
                try? await Task.sleep(nanoseconds: 20_000_000)
                continue
            }
            if let text = String(data: chunk, encoding: .utf8) {
                stderrText += text
                if stderrText.count > 8_000 {
                    stderrText = String(stderrText.suffix(4_000))
                }
            }
        }
    }

    private func handleLine(_ lineData: Data) {
        guard !lineData.isEmpty,
              let object = try? JSONSerialization.jsonObject(with: lineData),
              let payload = object as? [String: Any]
        else { return }

        let type = payload["type"] as? String
        if type == "ready" {
            sawReady = true
            ready = true
            statusText = (payload["detail"] as? String) ?? "Ready"
            readyContinuation?.resume()
            readyContinuation = nil
            return
        }
        if type == "error", payload["id"] == nil {
            let message = (payload["error"] as? String) ?? "unknown"
            statusText = "Error: \(message)"
            readyContinuation?.resume(throwing: WorkerClientError.spawnFailed(message))
            readyContinuation = nil
            return
        }
        if let id = payload["id"] as? String, let continuation = pending.removeValue(forKey: id) {
            continuation.resume(returning: payload)
        }
    }

    static func workerProjectURL() -> URL {
        if let override = ProcessInfo.processInfo.environment["GOD_WORKER_PROJECT"] {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let file = URL(fileURLWithPath: #filePath)
        return file
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("worker", isDirectory: true)
    }

    static func resolveUvURL() -> URL {
        if let override = ProcessInfo.processInfo.environment["GOD_UV"] {
            return URL(fileURLWithPath: override)
        }
        let candidates = [
            "/opt/homebrew/bin/uv",
            "/usr/local/bin/uv",
            "\(NSHomeDirectory())/.local/bin/uv",
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        return URL(fileURLWithPath: "/opt/homebrew/bin/uv")
    }

    static func workerEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let extras = ["/opt/homebrew/bin", "/usr/local/bin", "\(NSHomeDirectory())/.local/bin"]
        let existing = (env["PATH"] ?? "").split(separator: ":").map(String.init)
        var seen = Set<String>()
        var merged: [String] = []
        for part in extras + existing where !part.isEmpty && seen.insert(part).inserted {
            merged.append(part)
        }
        env["PATH"] = merged.joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        return env
    }
}
