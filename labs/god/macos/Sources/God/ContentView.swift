import SwiftUI

struct ContentView: View {
    @StateObject private var worker = WorkerClient()
    @StateObject private var voice = VoiceCapture()
    @StateObject private var speech = SpeechPlayer()
    @StateObject private var cursor = CursorOverlayController()
    @State private var session: SessionPhase = .idle
    @State private var log: [String] = ["God — conversational desktop operator. Mic fills Goal and auto-runs."]
    @State private var busy = false
    @State private var autoRunAfterMic = true
    @State private var goal = "Open Notes and type hello from God"
    @State private var policy = "Do not delete files. Stop if unsure."
    @State private var inputTokens = 0
    @State private var outputTokens = 0
    @State private var costUSD = 0.0
    @State private var whisperSeconds = 0.0

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

            usageBar

            Text(worker.statusText)
                .font(.callout)
                .foregroundStyle(.secondary)

            if voice.isRecording {
                Text("Recording… tap Stop Mic when finished speaking.")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.red)
            }
            if speech.isSpeaking {
                Text("Speaking…")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.blue)
            }

            HStack(spacing: 10) {
                Button("Start Session") { Task { await startSession() } }
                    .disabled(busy || session == .listening || session == .acting)
                Button(voice.isRecording ? "Stop Mic" : "Mic") { Task { await toggleMic() } }
                    .disabled(busy || !worker.ready || session == .idle || session == .stopped)
                Button("Run Goal") { Task { await runGoal() } }
                    .disabled(
                        busy
                            || voice.isRecording
                            || !worker.ready
                            || goal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || session == .idle
                            || session == .stopped
                    )
                Button("Test Click") { Task { await testClick() } }
                    .disabled(busy || voice.isRecording || !worker.ready || session == .stopped || session == .idle)
                Button("Stop", role: .destructive) { Task { await stopSession() } }
                    .disabled(busy || session == .idle || session == .stopped)
                    .keyboardShortcut(".", modifiers: [.command, .shift])
            }

            Toggle("Auto-run after Mic", isOn: $autoRunAfterMic)
                .toggleStyle(.switch)

            TextField("Goal", text: $goal, axis: .vertical)
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
            TextField("Policy (spoken do’s/don’ts)", text: $policy, axis: .vertical)
                .lineLimit(1...3)
                .textFieldStyle(.roundedBorder)

            Text("Mic → transcript → (optional) auto Run. Agent talks back. Red ring = mouse target. ⌘⇧. stops.")
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
        .onAppear { wireEvents() }
        .onReceive(NotificationCenter.default.publisher(for: .godStop)) { _ in
            Task { await stopSession() }
        }
    }

    private var usageBar: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Tokens \(inputTokens + outputTokens)")
                    .font(.caption.monospaced())
                Text("· in \(inputTokens) / out \(outputTokens)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(costLabel)
                    .font(.caption.weight(.semibold).monospaced())
            }
            ProgressView(value: Double(min(inputTokens + outputTokens, 200_000)), total: 200_000)
                .tint(.orange)
            Text("Projected ≈ \(costLabel) @ configured $/1M rates (env GOD_*_USD_PER_MTK). Whisper \(String(format: "%.1fs", whisperSeconds)).")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.12)))
    }

    private var costLabel: String {
        if costUSD < 0.01 {
            return String(format: "$%.4f", costUSD)
        }
        return String(format: "$%.3f", costUSD)
    }

    private var statusColor: Color {
        switch session {
        case .idle: return .gray
        case .listening: return voice.isRecording ? .red : .green
        case .acting: return .orange
        case .stopped: return .red
        }
    }

    private func wireEvents() {
        worker.onEvent = { payload in
            let type = payload["type"] as? String
            if type == "usage" {
                inputTokens = payload["input_tokens"] as? Int ?? inputTokens
                outputTokens = payload["output_tokens"] as? Int ?? outputTokens
                costUSD = payload["cost_usd"] as? Double ?? costUSD
                whisperSeconds = payload["whisper_seconds"] as? Double ?? whisperSeconds
                return
            }
            if type == "mouse" {
                if let x = payload["x"] as? Int, let y = payload["y"] as? Int {
                    cursor.move(toX: x, y: y)
                }
                return
            }
            if type == "speak" {
                if let text = payload["text"] as? String {
                    append("God: \(text)")
                }
                if let audio = payload["audio_base64"] as? String {
                    speech.play(base64: audio, format: (payload["format"] as? String) ?? "mp3")
                }
                return
            }
            if let message = payload["message"] as? String {
                append(message)
            } else if let detail = payload["detail"] as? String {
                append(detail)
            }
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
            cursor.show()
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
                append("Transcribing (\(data.count) bytes)…")
                let response = try await worker.send(
                    type: "transcribe",
                    extra: [
                        "audio_base64": data.base64EncodedString(),
                        "format": "wav",
                    ]
                )
                let text = (response["detail"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if text.isEmpty {
                    append("Heard nothing — try again, speak clearly for 2–3 seconds.")
                } else {
                    goal = text
                    append("Heard → Goal: \(text)")
                    if autoRunAfterMic {
                        await runGoal()
                    }
                }
            } catch {
                append("Mic failed: \(error.localizedDescription)")
            }
            return
        }
        do {
            try await voice.start()
            append("Listening… speak now, then tap Stop Mic.")
        } catch {
            append("Mic failed: \(error.localizedDescription)")
        }
    }

    private func runGoal() async {
        if voice.isRecording {
            _ = try? voice.stop()
        }
        let trimmed = goal.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            append("Goal is empty. Type one or use Mic first.")
            return
        }
        busy = true
        defer { busy = false }
        session = .acting
        cursor.show()
        append("Running goal: \(trimmed)")
        do {
            let final = try await worker.runAgent(goal: trimmed, policy: policy)
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
        cursor.show()
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
        speech.stop()
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
        cursor.hide()
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
