import SwiftUI

struct ContentView: View {
    @StateObject private var worker = WorkerClient()
    @StateObject private var voice = VoiceCapture()
    @StateObject private var speech = SpeechPlayer()
    @StateObject private var cursor = CursorOverlayController()
    @State private var session: SessionPhase = .idle
    @State private var log: [String] = ["God — realtime voice operator. Start Session, then just talk."]
    @State private var busy = false
    @State private var realtimeLive = false
    @State private var goal = ""
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

            if voice.isStreaming {
                Text("Realtime mic live — speak naturally.")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.green)
            }
            if speech.isSpeaking {
                Text("God speaking…")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(.blue)
            }

            HStack(spacing: 10) {
                Button("Start Session") { Task { await startSession() } }
                    .disabled(busy || session == .listening || session == .acting)
                Button(voice.isStreaming ? "Mute Mic" : "Mic Live") { Task { await toggleRealtimeMic() } }
                    .disabled(busy || !worker.ready || !realtimeLive || session == .idle || session == .stopped)
                Button("Send Text") { Task { await sendTextGoal() } }
                    .disabled(
                        busy
                            || !worker.ready
                            || !realtimeLive
                            || goal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || session == .idle
                            || session == .stopped
                    )
                Button("Test Click") { Task { await testClick() } }
                    .disabled(busy || !worker.ready || session == .stopped || session == .idle)
                Button("Stop", role: .destructive) { Task { await stopSession() } }
                    .disabled(busy || session == .idle || session == .stopped)
                    .keyboardShortcut(".", modifiers: [.command, .shift])
            }

            TextField("Say this as text (optional)", text: $goal, axis: .vertical)
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
            TextField("Policy (spoken do’s/don’ts)", text: $policy, axis: .vertical)
                .lineLimit(1...3)
                .textFieldStyle(.roundedBorder)

            Text("Realtime duplex voice + desktop tools. Move the mouse to pause God; say “continue” to resume. ⌘⇧. stops.")
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
            Text("Projected ≈ \(costLabel) @ GOD_*_USD_PER_MTK. Realtime audio billed as tokens.")
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
        case .listening: return voice.isStreaming ? .green : .yellow
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
            if type == "realtime_interrupt" {
                speech.stop()
                if let reason = payload["reason"] as? String {
                    append("Interrupted (\(reason)).")
                }
                return
            }
            if type == "speak" {
                let format = (payload["format"] as? String) ?? "mp3"
                if let text = payload["text"] as? String {
                    append("God: \(text)")
                }
                if let audio = payload["audio_base64"] as? String {
                    if format == "pcm16" {
                        let rate = payload["sample_rate"] as? Double ?? 24_000
                        speech.enqueuePCM(base64: audio, sampleRate: rate)
                    } else {
                        speech.play(base64: audio, format: format)
                    }
                }
                return
            }
            if payload["partial"] as? Bool == true {
                return
            }
            if let message = payload["message"] as? String {
                append(message)
            } else if let detail = payload["detail"] as? String {
                append(detail)
            }
        }

        voice.onPCMChunk = { data in
            Task { @MainActor in
                guard realtimeLive, worker.ready else { return }
                try? worker.sendUnordered(
                    type: "realtime_audio_in",
                    extra: ["audio_base64": data.base64EncodedString()]
                )
            }
        }
    }

    private func append(_ line: String) {
        log.append(line)
        if log.count > 400 {
            log.removeFirst(log.count - 300)
        }
    }

    private func startSession() async {
        busy = true
        defer { busy = false }
        append("Starting realtime session…")
        do {
            try await worker.start()
            session = .listening
            cursor.show()
            _ = try await worker.send(type: "realtime_start", extra: ["policy": policy])
            realtimeLive = true
            try await voice.startStreaming()
            append("Session open — realtime mic live. Just talk.")
        } catch {
            realtimeLive = false
            voice.stopStreaming()
            session = .idle
            append("Start failed: \(error.localizedDescription)")
        }
    }

    private func toggleRealtimeMic() async {
        if voice.isStreaming {
            voice.stopStreaming()
            append("Mic muted.")
            return
        }
        do {
            try await voice.startStreaming()
            append("Mic live again.")
        } catch {
            append("Mic failed: \(error.localizedDescription)")
        }
    }

    private func sendTextGoal() async {
        let trimmed = goal.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        busy = true
        defer { busy = false }
        session = .acting
        append("You (text): \(trimmed)")
        do {
            _ = try await worker.send(type: "realtime_text", extra: ["text": trimmed])
            goal = ""
            session = .listening
        } catch {
            append("Send failed: \(error.localizedDescription)")
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
        voice.stopStreaming()
        speech.stop()
        realtimeLive = false
        do {
            if worker.ready {
                _ = try? await worker.send(type: "realtime_stop")
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
        case .listening: return "Realtime open"
        case .acting: return "Acting"
        case .stopped: return "Stopped"
        }
    }
}
