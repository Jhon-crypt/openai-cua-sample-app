import SwiftUI

struct ContentView: View {
    @StateObject private var worker = WorkerClient()
    @StateObject private var voice = VoiceCapture()
    @State private var session: SessionPhase = .idle
    @State private var log: [String] = ["God — type a goal or hold Mic, then Run."]
    @State private var busy = false
    @State private var goal = "Open Notes and type hello from God"
    @State private var policy = "Do not delete files. Stop if unsure."

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Circle()
                    .fill(statusColor)
                    .frame(width: 10, height: 10)
                Text("God")
                    .font(.title2.weight(.semibold))
                Spacer()
                Text(session.label)
                    .foregroundStyle(.secondary)
            }

            Text(worker.statusText)
                .font(.callout)
                .foregroundStyle(.secondary)

            HStack(spacing: 10) {
                Button("Start Session") { Task { await startSession() } }
                    .disabled(busy || session == .listening || session == .acting)
                Button(voice.isRecording ? "Stop Mic" : "Mic") { Task { await toggleMic() } }
                    .disabled(busy || !worker.ready || session == .idle || session == .stopped)
                Button("Run Goal") { Task { await runGoal() } }
                    .disabled(busy || !worker.ready || goal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || session == .idle || session == .stopped)
                Button("Test Click") { Task { await testClick() } }
                    .disabled(busy || !worker.ready || session == .stopped || session == .idle)
                Button("Stop", role: .destructive) { Task { await stopSession() } }
                    .disabled(busy || session == .idle || session == .stopped)
                    .keyboardShortcut(".", modifiers: [.command, .shift])
            }

            TextField("Goal", text: $goal, axis: .vertical)
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
            TextField("Policy (spoken do’s/don’ts)", text: $policy, axis: .vertical)
                .lineLimit(1...3)
                .textFieldStyle(.roundedBorder)

            Text("Kill switch: ⌘⇧. — Mic records a clip, then Whisper turns it into a goal.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(log.enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
        .padding(20)
        .onAppear {
            worker.onEvent = { payload in
                if let message = payload["message"] as? String {
                    append(message)
                } else if let detail = payload["detail"] as? String {
                    append(detail)
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .godStop)) { _ in
            Task { await stopSession() }
        }
    }

    private var statusColor: Color {
        switch session {
        case .idle: return .gray
        case .listening: return .green
        case .acting: return .orange
        case .stopped: return .red
        }
    }

    private func append(_ line: String) {
        log.append(line)
    }

    private func startSession() async {
        busy = true
        defer { busy = false }
        append("Starting session…")
        do {
            try await worker.start()
            session = .listening
            append("Session open — worker ready.")
        } catch {
            session = .idle
            append("Start failed: \(error.localizedDescription)")
        }
    }

    private func toggleMic() async {
        if voice.isRecording {
            busy = true
            defer { busy = false }
            do {
                let data = try voice.stop()
                append("Transcribing…")
                let response = try await worker.send(
                    type: "transcribe",
                    extra: [
                        "audio_base64": data.base64EncodedString(),
                        "format": "m4a",
                    ]
                )
                let text = (response["detail"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if text.isEmpty {
                    append("Heard nothing.")
                } else {
                    goal = text
                    append("Heard: \(text)")
                }
            } catch {
                append("Mic failed: \(error.localizedDescription)")
            }
            return
        }
        do {
            try await voice.start()
            append("Listening… tap Stop Mic when done.")
        } catch {
            append("Mic failed: \(error.localizedDescription)")
        }
    }

    private func runGoal() async {
        busy = true
        defer { busy = false }
        session = .acting
        append("Running goal: \(goal)")
        do {
            let final = try await worker.runAgent(goal: goal, policy: policy)
            append("Final: \(final)")
            session = .listening
        } catch {
            append("Agent failed: \(error.localizedDescription)")
            session = worker.ready ? .listening : .stopped
        }
    }

    private func testClick() async {
        busy = true
        defer { busy = false }
        session = .acting
        do {
            let response = try await worker.send(type: "click_center")
            let detail = (response["detail"] as? String) ?? String(describing: response)
            append("click_center → \(detail)")
            session = .listening
        } catch {
            append("click failed: \(error.localizedDescription)")
            session = worker.ready ? .listening : .stopped
        }
    }

    private func stopSession() async {
        busy = true
        defer { busy = false }
        if voice.isRecording {
            _ = try? voice.stop()
        }
        do {
            if worker.ready {
                _ = try? await worker.send(type: "agent_stop")
                let response = try await worker.send(type: "stop")
                let detail = (response["detail"] as? String) ?? "released"
                append("stop → \(detail)")
            }
        } catch {
            append("stop send: \(error.localizedDescription)")
        }
        worker.stopWorker()
        session = .stopped
        append("Session stopped.")
    }
}

private enum SessionPhase {
    case idle, listening, acting, stopped

    var label: String {
        switch self {
        case .idle: return "Idle"
        case .listening: return "Session open"
        case .acting: return "Acting"
        case .stopped: return "Stopped"
        }
    }
}
