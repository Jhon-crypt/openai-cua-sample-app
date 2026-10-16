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
    private var pending: [String: CheckedContinuation<[String: Any], Error>] = [:]
    private var readyContinuation: CheckedContinuation<Void, Error>?
    private var buffer = Data()

    func start() async throws {
        if process?.isRunning == true, ready { return }
        stopWorker()

        let workerProject = Self.workerProjectURL()
        let process = Process()
        let stdin = Pipe()
        let stdout = Pipe()

        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            "uv", "run", "--project", workerProject.path, "python", "-m", "god_worker",
        ]
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = Pipe()
        process.terminationHandler = { [weak self] _ in
            Task { @MainActor in self?.handleTermination() }
        }

        do {
            try process.run()
        } catch {
            throw WorkerClientError.spawnFailed(error.localizedDescription)
        }

        self.process = process
        self.stdinPipe = stdin
        self.statusText = "Starting worker…"
        self.ready = false

        readTask = Task { await self.readLoop(handle: stdout.fileHandleForReading) }

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            self.readyContinuation = continuation
            Task {
                try await Task.sleep(nanoseconds: 60_000_000_000)
                if let pendingReady = self.readyContinuation {
                    self.readyContinuation = nil
                    pendingReady.resume(throwing: WorkerClientError.timeout)
                }
            }
        }
    }

    func stopWorker() {
        if process?.isRunning == true {
            if let stdin = stdinPipe {
                let payload: [String: Any] = ["type": "stop", "id": "shutdown"]
                if let data = try? JSONSerialization.data(withJSONObject: payload) {
                    var line = data
                    line.append(0x0A)
                    try? stdin.fileHandleForWriting.write(contentsOf: line)
                    Thread.sleep(forTimeInterval: 0.15)
                }
            }
            process?.terminate()
        }
        readTask?.cancel()
        handleTermination()
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

    private func handleTermination() {
        ready = false
        statusText = "Disconnected"
        process = nil
        stdinPipe = nil
        if let readyContinuation {
            self.readyContinuation = nil
            readyContinuation.resume(throwing: WorkerClientError.notRunning)
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

    private func handleLine(_ lineData: Data) {
        guard !lineData.isEmpty,
              let object = try? JSONSerialization.jsonObject(with: lineData),
              let payload = object as? [String: Any]
        else { return }

        let type = payload["type"] as? String
        if type == "ready" {
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
}
