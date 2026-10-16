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
    var onEvent: (([String: Any]) -> Void)?

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var pending: [String: CheckedContinuation<[String: Any], Error>] = [:]
    private var agentCompletions: [String: CheckedContinuation<String, Error>] = [:]
    private var readyContinuation: CheckedContinuation<Void, Error>?
    private var buffer = Data()
    private var stderrText = ""
    private var sawReady = false
    private var timeoutTask: Task<Void, Never>?

    func start() async throws {
        if process?.isRunning == true, ready { return }
        stopWorker()

        statusText = "Starting worker…"
        let workerProject = Self.workerProjectURL()
        let marker = workerProject.appendingPathComponent("pyproject.toml")
        guard FileManager.default.fileExists(atPath: marker.path) else {
            throw WorkerClientError.spawnFailed("Worker project missing at \(workerProject.path)")
        }

        let process = Process()
        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        let launch = Self.launchConfiguration(workerProject: workerProject)

        process.executableURL = launch.executable
        process.arguments = launch.arguments
        process.environment = Self.workerEnvironment()
        process.currentDirectoryURL = workerProject
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        process.terminationHandler = { [weak self] proc in
            Task { @MainActor in self?.handleTermination(exitCode: proc.terminationStatus) }
        }

        sawReady = false
        stderrText = ""
        buffer = Data()
        readyContinuation = nil
        statusText = "Starting \(launch.executable.lastPathComponent)…"

        attachReaders(stdout: stdout, stderr: stderr)
        try await startProcess(process, stdin: stdin, stdout: stdout, stderr: stderr, launchLabel: launch.display)
    }

    private func startProcess(
        _ process: Process,
        stdin: Pipe,
        stdout: Pipe,
        stderr: Pipe,
        launchLabel: String
    ) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            self.readyContinuation = continuation
            self.timeoutTask = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                guard !Task.isCancelled else { return }
                if let pendingReady = self.readyContinuation {
                    self.readyContinuation = nil
                    let detail = self.stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
                    let message = detail.isEmpty
                        ? "Timed out after 20s starting worker (\(launchLabel))."
                        : "Timed out: \(detail)"
                    self.statusText = message
                    pendingReady.resume(throwing: WorkerClientError.spawnFailed(message))
                }
            }

            do {
                try process.run()
                self.process = process
                self.stdinPipe = stdin
                self.stdoutPipe = stdout
                self.stderrPipe = stderr
                self.ready = false
                self.statusText = "Waiting for worker ready…"
            } catch {
                self.timeoutTask?.cancel()
                self.readyContinuation = nil
                continuation.resume(throwing: WorkerClientError.spawnFailed(error.localizedDescription))
            }
        }
        timeoutTask?.cancel()
        timeoutTask = nil
    }

    func stopWorker() {
        timeoutTask?.cancel()
        timeoutTask = nil
        detachReaders()

        let running = process
        if running?.isRunning == true {
            if let stdin = stdinPipe {
                let payload: [String: Any] = ["type": "stop", "id": "shutdown"]
                if let data = try? JSONSerialization.data(withJSONObject: payload) {
                    var line = data
                    line.append(0x0A)
                    try? stdin.fileHandleForWriting.write(contentsOf: line)
                }
            }
            running?.terminate()
        }
        handleTermination(exitCode: running?.terminationStatus)
    }

    func send(type: String, extra: [String: Any] = [:]) async throws -> [String: Any] {
        guard stdinPipe != nil, process?.isRunning == true, ready else {
            throw WorkerClientError.notRunning
        }
        let id = UUID().uuidString
        var payload: [String: Any] = ["type": type, "id": id]
        for (key, value) in extra { payload[key] = value }

        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try writePayload(payload)
            } catch {
                pending.removeValue(forKey: id)
                continuation.resume(throwing: error)
            }
        }
    }

    func runAgent(goal: String, policy: String = "", maxTurns: Int = 12) async throws -> String {
        guard stdinPipe != nil, process?.isRunning == true, ready else {
            throw WorkerClientError.notRunning
        }
        let id = UUID().uuidString
        let payload: [String: Any] = [
            "type": "agent_run",
            "id": id,
            "goal": goal,
            "policy": policy,
            "max_turns": maxTurns,
        ]
        return try await withCheckedThrowingContinuation { continuation in
            agentCompletions[id] = continuation
            do {
                try writePayload(payload)
            } catch {
                agentCompletions.removeValue(forKey: id)
                continuation.resume(throwing: error)
            }
        }
    }

    private func writePayload(_ payload: [String: Any]) throws {
        guard let stdin = stdinPipe else { throw WorkerClientError.notRunning }
        let data = try JSONSerialization.data(withJSONObject: payload)
        var line = data
        line.append(0x0A)
        try stdin.fileHandleForWriting.write(contentsOf: line)
    }

    private func attachReaders(stdout: Pipe, stderr: Pipe) {
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            Task { @MainActor in
                self?.consumeStdout(chunk)
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            Task { @MainActor in
                self?.consumeStderr(chunk)
            }
        }
    }

    private func detachReaders() {
        stdoutPipe?.fileHandleForReading.readabilityHandler = nil
        stderrPipe?.fileHandleForReading.readabilityHandler = nil
    }

    private func consumeStdout(_ chunk: Data) {
        buffer.append(chunk)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let lineData = Data(buffer[..<newline])
            buffer.removeSubrange(...newline)
            handleLine(lineData)
        }
    }

    private func consumeStderr(_ chunk: Data) {
        if let text = String(data: chunk, encoding: .utf8) {
            stderrText += text
            if stderrText.count > 8_000 {
                stderrText = String(stderrText.suffix(4_000))
            }
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty, !ready {
                statusText = "Worker: \(trimmed.prefix(120))"
            }
        }
    }

    private func handleTermination(exitCode: Int32?) {
        detachReaders()
        timeoutTask?.cancel()
        timeoutTask = nil
        ready = false
        process = nil
        stdinPipe = nil
        stdoutPipe = nil
        stderrPipe = nil

        if let readyContinuation {
            self.readyContinuation = nil
            let detail = stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
            let code = exitCode.map(String.init) ?? "?"
            let message = detail.isEmpty
                ? "Worker exited before ready (code \(code))."
                : "Worker exited (code \(code)): \(detail)"
            statusText = message
            readyContinuation.resume(throwing: WorkerClientError.spawnFailed(message))
        } else if !ready {
            statusText = "Disconnected"
        }

        for (_, continuation) in pending {
            continuation.resume(throwing: WorkerClientError.notRunning)
        }
        pending.removeAll()
        for (_, continuation) in agentCompletions {
            continuation.resume(throwing: WorkerClientError.notRunning)
        }
        agentCompletions.removeAll()
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
            timeoutTask?.cancel()
            timeoutTask = nil
            readyContinuation?.resume()
            readyContinuation = nil
            return
        }
        if type == "agent_event" {
            onEvent?(payload)
            if let message = payload["message"] as? String {
                statusText = message
            }
            return
        }
        if type == "ack" {
            onEvent?(payload)
            return
        }
        if type == "error", payload["id"] == nil {
            let message = (payload["error"] as? String) ?? "unknown"
            statusText = "Error: \(message)"
            timeoutTask?.cancel()
            timeoutTask = nil
            readyContinuation?.resume(throwing: WorkerClientError.spawnFailed(message))
            readyContinuation = nil
            return
        }
        if let id = payload["id"] as? String {
            if let agentWait = agentCompletions.removeValue(forKey: id) {
                if type == "error" || payload["ok"] as? Bool == false {
                    agentWait.resume(throwing: WorkerClientError.spawnFailed((payload["error"] as? String) ?? "agent failed"))
                } else {
                    agentWait.resume(returning: (payload["detail"] as? String) ?? "done")
                }
                return
            }
            if let continuation = pending.removeValue(forKey: id) {
                if type == "error" || payload["ok"] as? Bool == false {
                    continuation.resume(throwing: WorkerClientError.spawnFailed((payload["error"] as? String) ?? "request failed"))
                } else {
                    continuation.resume(returning: payload)
                }
            }
        }
    }

    private struct LaunchConfiguration {
        var executable: URL
        var arguments: [String]
        var display: String
    }

    private static func launchConfiguration(workerProject: URL) -> LaunchConfiguration {
        let venvPython = workerProject.appendingPathComponent(".venv/bin/python")
        if FileManager.default.isExecutableFile(atPath: venvPython.path) {
            return LaunchConfiguration(
                executable: venvPython,
                arguments: ["-m", "god_worker"],
                display: venvPython.path
            )
        }
        let uv = resolveUvURL()
        return LaunchConfiguration(
            executable: uv,
            arguments: ["run", "--project", workerProject.path, "python", "-m", "god_worker"],
            display: "\(uv.lastPathComponent) run"
        )
    }

    static func workerProjectURL() -> URL {
        if let override = ProcessInfo.processInfo.environment["GOD_WORKER_PROJECT"] {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let fm = FileManager.default
        for _ in 0..<8 {
            let candidate = directory.appendingPathComponent("worker", isDirectory: true)
            let marker = candidate.appendingPathComponent("pyproject.toml")
            if fm.fileExists(atPath: marker.path) {
                return candidate
            }
            let parent = directory.deletingLastPathComponent()
            if parent.path == directory.path { break }
            directory = parent
        }
        return URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
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
        for (key, value) in loadDotEnv() {
            if env[key] == nil || env[key]?.isEmpty == true {
                env[key] = value
            }
        }
        return env
    }

    static func loadDotEnv() -> [String: String] {
        let worker = workerProjectURL()
        let rootEnv = worker.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent(".env")
        // labs/god/worker -> labs/god -> labs -> repo root is one more up from god
        let candidates = [
            worker.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent(".env"),
            rootEnv,
            worker.appendingPathComponent(".env"),
        ]
        var values: [String: String] = [:]
        for url in candidates {
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            for raw in text.split(separator: "\n") {
                let line = raw.trimmingCharacters(in: .whitespaces)
                if line.isEmpty || line.hasPrefix("#") || !line.contains("=") { continue }
                let parts = line.split(separator: "=", maxSplits: 1).map(String.init)
                guard parts.count == 2 else { continue }
                var value = parts[1].trimmingCharacters(in: .whitespaces)
                if (value.hasPrefix("\"") && value.hasSuffix("\"")) || (value.hasPrefix("'") && value.hasSuffix("'")) {
                    value = String(value.dropFirst().dropLast())
                }
                values[parts[0].trimmingCharacters(in: .whitespaces)] = value
            }
            if values["OPENAI_API_KEY"] != nil { break }
        }
        return values
    }
}
