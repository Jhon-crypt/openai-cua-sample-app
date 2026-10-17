import AVFoundation
import Foundation

/// Thread-safe bool readable from the audio capture path (off MainActor).
private final class AtomicBool: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Bool

    init(_ value: Bool = false) {
        self.value = value
    }

    func set(_ newValue: Bool) {
        lock.lock()
        value = newValue
        lock.unlock()
    }

    func get() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

@MainActor
final class SpeechPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var isSpeaking = false

    /// Mic must stay muted while we play (+ hangover) or speaker echo barges in and cuts God off.
    private let suppressMic = AtomicBool(false)
    private var hangoverTask: Task<Void, Never>?
    private var speakingWatchdog: Task<Void, Never>?

    private var player: AVAudioPlayer?
    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    private var pcmFormat: AVAudioFormat?
    private var scheduledBuffers = 0

    nonisolated func shouldSuppressMic() -> Bool {
        suppressMic.get()
    }

    // MARK: - File / compressed (mp3 TTS)

    func play(base64: String, format: String = "mp3") {
        if format == "pcm16" {
            enqueuePCM(base64: base64, sampleRate: 24_000)
            return
        }
        guard let data = Data(base64Encoded: base64) else { return }
        do {
            stopPCM()
            player?.stop()
            let player = try AVAudioPlayer(data: data)
            player.delegate = self
            self.player = player
            markSpeaking()
            player.play()
        } catch {
            clearSpeakingAfterHangover(immediate: true)
        }
    }

    // MARK: - Streaming pcm16

    func enqueuePCM(base64: String, sampleRate: Double = 24_000) {
        guard let data = Data(base64Encoded: base64), !data.isEmpty else { return }
        ensurePCMEngine(sampleRate: sampleRate)
        guard let playerNode, let pcmFormat else { return }

        let frameCount = data.count / MemoryLayout<Int16>.size
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: pcmFormat, frameCapacity: AVAudioFrameCount(frameCount))
        else { return }
        buffer.frameLength = AVAudioFrameCount(frameCount)
        data.withUnsafeBytes { raw in
            guard let src = raw.bindMemory(to: Int16.self).baseAddress,
                  let dst = buffer.int16ChannelData?[0] else { return }
            dst.update(from: src, count: frameCount)
        }

        hangoverTask?.cancel()
        hangoverTask = nil
        scheduledBuffers += 1
        markSpeaking()
        bumpSpeakingWatchdog()
        playerNode.scheduleBuffer(buffer) { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.scheduledBuffers = max(0, self.scheduledBuffers - 1)
                if self.scheduledBuffers == 0 {
                    self.clearSpeakingAfterHangover(immediate: false)
                }
            }
        }
        if !playerNode.isPlaying {
            playerNode.play()
        }
    }

    func stop() {
        speakingWatchdog?.cancel()
        speakingWatchdog = nil
        hangoverTask?.cancel()
        hangoverTask = nil
        player?.stop()
        stopPCM()
        isSpeaking = false
        suppressMic.set(false)
    }

    private func markSpeaking() {
        isSpeaking = true
        suppressMic.set(true)
    }

    /// Drop isSpeaking for UI, but keep mic muted briefly so echo doesn't trip server VAD.
    private func clearSpeakingAfterHangover(immediate: Bool) {
        isSpeaking = false
        suppressMic.set(true)
        hangoverTask?.cancel()
        if immediate {
            suppressMic.set(false)
            return
        }
        hangoverTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 450_000_000)
            guard !Task.isCancelled else { return }
            if self.scheduledBuffers == 0, self.player?.isPlaying != true {
                self.suppressMic.set(false)
            }
        }
    }

    /// Only recovers stuck UI state — never cancels in-flight PCM buffers.
    private func bumpSpeakingWatchdog() {
        speakingWatchdog?.cancel()
        speakingWatchdog = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            guard !Task.isCancelled else { return }
            if self.scheduledBuffers == 0, self.player?.isPlaying != true {
                self.clearSpeakingAfterHangover(immediate: false)
            }
        }
    }

    private func ensurePCMEngine(sampleRate: Double) {
        if engine != nil, pcmFormat?.sampleRate == sampleRate { return }
        stopPCM()
        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: sampleRate,
            channels: 1,
            interleaved: true
        ) else { return }

        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        do {
            try engine.start()
            self.engine = engine
            self.playerNode = node
            self.pcmFormat = format
        } catch {
            self.engine = nil
            self.playerNode = nil
            self.pcmFormat = nil
        }
    }

    private func stopPCM() {
        playerNode?.stop()
        engine?.stop()
        playerNode = nil
        engine = nil
        pcmFormat = nil
        scheduledBuffers = 0
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.clearSpeakingAfterHangover(immediate: false)
        }
    }
}
