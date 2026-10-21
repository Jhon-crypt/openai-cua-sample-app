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
    @StateObject private var worker: WorkerClient
    @StateObject private var voice: VoiceCapture
    @StateObject private var speech: SpeechPlayer
    @StateObject private var cursor: CursorOverlayController
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
    @State private var audioProfile = ""

    init() {
        _worker = StateObject(wrappedValue: WorkerClient())
        _voice = StateObject(wrappedValue: VoiceCapture())
        _speech = StateObject(wrappedValue: SpeechPlayer())
        _cursor = StateObject(wrappedValue: CursorOverlayController())
    }

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
                Text(micStatusLabel)
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

            Text("Ask God to do something on the Mac; it stays quiet otherwise. Say “stop” to cancel. Moving the mouse stops the current task. ⌘⇧. ends the session.")
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
        // Do NOT use Timer.publish(...).autoconnect() inside body — Swift 6 recreates it
        // every render and traps in isMainExecutor (bus error). Structured task is safe.
        .task(id: realtimeLive) {
            guard realtimeLive else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                guard !Task.isCancelled, realtimeLive else { break }
                await syncAudioProfileIfNeeded()
            }
        }
    }

    private var micStatusLabel: String {
        if audioProfile == "far_field" {
            return "Mic live — talk over God clearly to interrupt (laptop speakers)."
        }
        return "Mic live — speak naturally."
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
            Text("Projected ≈ \(costLabel). Live voice billed per minute; desktop agent per token.")
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
                if reason != "mouse" {
                    speech.stop()
                }
                if let reason {
                    append(reason == "speech" ? "Playback cut: God yielded to your voice." : "Interrupted (\(reason)).")
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

        // Stream full PCM to Realtime (NR + semantic VAD). Only duck on laptop speakers (echo).
        let ingress = worker.audioIngress
        let player = speech
        voice.onPCMChunk = { @Sendable data in
            // Live's transcript/delegation offsets are positions in the input stream: send
            // silence (not nothing) while ducked so the timeline stays continuous.
            ingress.sendPCMBase64(player.micChunkToSend(data).base64EncodedString())
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
        append("Starting voice session…")
        do {
            try await worker.start()
            session = .listening
            cursor.show()
            let nr = OutputRoute.realtimeNoiseReduction()
            _ = try await worker.send(
                type: "realtime_start",
                extra: ["policy": policy, "noise_reduction": nr]
            )
            audioProfile = nr
            realtimeLive = true
            worker.setRealtimeAudioEnabled(true)
            try await voice.startStreaming()
            append(nr == "far_field" ? "Mic on — laptop speakers." : "Mic on — headphones.")
        } catch {
            realtimeLive = false
            worker.setRealtimeAudioEnabled(false)
            voice.stopStreaming()
            audioProfile = ""
            session = .idle
            append("Start failed: \(error.localizedDescription)")
        }
    }

    /// Hot-swap NR/VAD when AirPods connect/disconnect mid-session.
    private func syncAudioProfileIfNeeded() async {
        guard realtimeLive, worker.ready else { return }
        let nr = OutputRoute.realtimeNoiseReduction()
        guard nr != audioProfile else { return }
        do {
            _ = try await worker.send(
                type: "realtime_audio_profile",
                extra: ["noise_reduction": nr]
            )
            audioProfile = nr
            append("Audio profile → \(nr).")
        } catch {
            append("Audio profile update failed: \(error.localizedDescription)")
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
        audioProfile = ""
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
        case .listening: return "Session open"
        case .acting: return "Acting"
        case .stopped: return "Stopped"
        }
    }
}
