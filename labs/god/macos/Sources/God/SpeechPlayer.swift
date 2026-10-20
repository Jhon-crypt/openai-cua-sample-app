import AVFoundation
import CoreAudio
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

    /// Only used on speaker output. Headphones/BT skip this so duplex stays open.
    private let suppressMic = AtomicBool(false)
    /// True while local God audio is playing (even on headphones where mic is not ducked).
    private let playbackActive = AtomicBool(false)
    private var hangoverTask: Task<Void, Never>?
    private var speakingWatchdog: Task<Void, Never>?
    private var lastEnqueueAt = Date.distantPast

    private var player: AVAudioPlayer?
    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    private var pcmFormat: AVAudioFormat?
    private var scheduledBuffers = 0

    /// Required for `@StateObject` under Swift 6 / macOS 26 — MainActor-isolated init crashes body eval.
    nonisolated override init() {
        super.init()
    }

    nonisolated func shouldSuppressMic() -> Bool {
        suppressMic.get()
    }

    nonisolated func isPlaybackActive() -> Bool {
        playbackActive.get()
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
            bumpSpeakingWatchdog()
        } catch {
            forceMicOpen()
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
        lastEnqueueAt = Date()
        scheduledBuffers += 1
        markSpeaking()
        bumpSpeakingWatchdog()
        playerNode.scheduleBuffer(buffer) { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.scheduledBuffers = max(0, self.scheduledBuffers - 1)
                if self.scheduledBuffers == 0 {
                    self.clearSpeakingAfterHangover()
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
        forceMicOpen()
    }

    private func markSpeaking() {
        isSpeaking = true
        playbackActive.set(true)
        // Headphones/BT: no speaker bleed — keep mic open so operator can barge in / reply.
        if OutputRoute.shouldDuckMicForEcho() {
            suppressMic.set(true)
        } else {
            suppressMic.set(false)
        }
    }

    private func clearSpeakingAfterHangover() {
        isSpeaking = false
        playbackActive.set(false)
        guard OutputRoute.shouldDuckMicForEcho() else {
            suppressMic.set(false)
            return
        }
        suppressMic.set(true)
        hangoverTask?.cancel()
        hangoverTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else { return }
            if self.scheduledBuffers == 0 {
                self.suppressMic.set(false)
            }
        }
    }

    /// Buffer completions are unreliable on AVAudioPlayerNode — never stay deaf.
    private func bumpSpeakingWatchdog() {
        speakingWatchdog?.cancel()
        speakingWatchdog = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard !Task.isCancelled else { return }
            let idle = Date().timeIntervalSince(self.lastEnqueueAt)
            // No new PCM for 1s ⇒ utterance over (or completions dropped). Re-open mic.
            if idle >= 0.95 {
                self.scheduledBuffers = 0
                self.forceMicOpen()
            }
        }
    }

    private func forceMicOpen() {
        isSpeaking = false
        playbackActive.set(false)
        suppressMic.set(false)
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
            self.clearSpeakingAfterHangover()
        }
    }
}

/// Built-in *speakers* need mic ducking; headphones / BT / USB headsets do not.
enum OutputRoute {
    /// OpenAI Realtime: near_field for close mics (AirPods), far_field for laptop/room mics.
    static func realtimeNoiseReduction() -> String {
        shouldDuckMicForEcho() ? "far_field" : "near_field"
    }

    static func shouldDuckMicForEcho() -> Bool {
        guard let deviceID = defaultOutputDeviceID() else {
            // Unknown → prefer hearing the operator over echo protection.
            return false
        }
        let transport = transportType(of: deviceID)
        // AirPods / BT / USB interfaces never need speaker-echo ducking.
        if transport == kAudioDeviceTransportTypeBluetooth
            || transport == kAudioDeviceTransportTypeBluetoothLE
            || transport == kAudioDeviceTransportTypeUSB
        {
            return false
        }
        // MacBook built-in: same device for speakers and wired headphones — check data source.
        if let source = outputDataSource(of: deviceID) {
            // 'ispk' = internal speakers; 'hdpn' = headphones jack.
            if source == fourCC("hdpn") { return false }
            if source == fourCC("ispk") { return true }
        }
        // Built-in with unknown source → duck (classic laptop speakers).
        return transport == kAudioDeviceTransportTypeBuiltIn
    }

    private static func defaultOutputDeviceID() -> AudioDeviceID? {
        var deviceID = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            0,
            nil,
            &size,
            &deviceID
        )
        guard status == noErr, deviceID != kAudioObjectUnknown else { return nil }
        return deviceID
    }

    private static func transportType(of deviceID: AudioDeviceID) -> UInt32? {
        var transport = UInt32(0)
        var size = UInt32(MemoryLayout<UInt32>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyTransportType,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &transport)
        return status == noErr ? transport : nil
    }

    private static func outputDataSource(of deviceID: AudioDeviceID) -> UInt32? {
        var source = UInt32(0)
        var size = UInt32(MemoryLayout<UInt32>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDataSource,
            mScope: kAudioDevicePropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &source)
        return status == noErr ? source : nil
    }

    private static func fourCC(_ s: String) -> UInt32 {
        var value: UInt32 = 0
        for byte in s.utf8.prefix(4) {
            value = (value << 8) | UInt32(byte)
        }
        return value
    }
}
