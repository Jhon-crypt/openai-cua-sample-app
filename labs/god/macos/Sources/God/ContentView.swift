import SwiftUI

private struct LogLine: Identifiable, Equatable {
    let id: UUID
    let text: String

    init(_ text: String) {
        self.id = UUID()
        self.text = text
    }
}

struct ContentView: View {
    @StateObject private var worker = WorkerClient()
    @StateObject private var voice = VoiceCapture()
    @StateObject private var speech = SpeechPlayer()
    @StateObject private var cursor = CursorOverlayController()
    @State private var session: SessionPhase = .idle
    @State private var log: [LogLine] = [LogLine("God — realtime voice operator. Start Session, then just talk.")]
    @State private var busy = false
    @State private var realtimeLive = false
    @State private var goal = ""
    @State private var policy = "Do not delete files. Stop if unsure. Prefer confirming before irreversible actions."
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

            Text("Any Mac app via open_app + clicks/typing. Red ring follows the agent mouse. Move mouse to pause; say “continue”. ⌘⇧. stops.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Divider()

            ScrollViewReader { proxy in
                ScrollView {
                    // Non-lazy stack + stable UUID ids — avoids ForEachChild crash when the log updates fast.
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(log) { line in
                            Text(line.text)
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(line.id)
                        }
                    }
                }
                .frame(maxHeight: .infinity)
                .onChange(of: log.count) { _, _ in
                    if let last = log.last?.id {
                        proxy.scrollTo(last, anchor: .bottom)
                    }
                }
            }
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
                session = .acting
                if let x = payload["x"] as? Int, let y = payload["y"] as? Int {
                    cursor.move(toX: x, y: y)
                }
                return
            }
            if type == "realtime_interrupt" {
                let reason = payload["reason"] as? String
                // Mouse pause only freezes desktop tools — don't chop mid-sentence audio.
                // Speech barge-in (real operator talk) still stops local playback.
                if reason != "mouse" {
                    speech.stop()
                }
                if let reason, reason != "speech" {
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
            } else if let detail = payload["detail"] as? String,
                      detail != "audio queued",
                      detail != "ok" {
                append(detail)
            }
        }

        // Off-MainActor path: drop chunks while local audio plays (echo → VAD barge-in → cutoff),
        // and write stdin without Task-per-chunk (that flooded MainActor and crashed the HUD).
        let ingress = worker.audioIngress
        let player = speech
        voice.onPCMChunk = { @Sendable data in
            if player.shouldSuppressMic() { return }
            ingress.sendPCMBase64(data.base64EncodedString())
        }
    }

    private func append(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        log.append(LogLine(trimmed))
        if log.count > 200 {
            log.removeFirst(log.count - 150)
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
            worker.setRealtimeAudioEnabled(true)
            try await voice.startStreaming()
            append("Session open — realtime mic live. Just talk.")
        } catch {
            realtimeLive = false
            worker.setRealtimeAudioEnabled(false)
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
        cursor.show()
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
        worker.setRealtimeAudioEnabled(false)
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
