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

/// Laptop speakers have no echo cancellation, so while God is audible the mic hears God.
/// Pass only mic audio clearly louder than that echo (the operator talking over God) so
/// Live hears the interruption; everything else becomes silence.
private final class BargeInGate: @unchecked Sendable {
    private let lock = NSLock()
    /// Running estimate of God's echo level in the mic (pcm RMS, 0…1). Measured ≈ 0.006 at normal volume.
    private var echoLevel: Float = 0.006
    private var openUntil = Date.distantPast

    private static let minThreshold: Float = 0.025
    private static let echoMultiple: Float = 4
    /// Keep passing audio briefly after the last loud chunk so word endings aren't chopped.
    private static let hangover: TimeInterval = 0.6

    func filter(_ chunk: Data) -> Data {
        let rms = Self.rms(chunk)
        let now = Date()
        lock.lock()
        defer { lock.unlock() }
        if rms >= max(Self.minThreshold, echoLevel * Self.echoMultiple) {
            openUntil = now.addingTimeInterval(Self.hangover)
        }
        if now < openUntil { return chunk }
        echoLevel = echoLevel * 0.95 + rms * 0.05
        return Data(count: chunk.count)
    }

    private static func rms(_ chunk: Data) -> Float {
        chunk.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            guard !samples.isEmpty else { return 0 }
            var sum: Float = 0
            for s in samples {
                let v = Float(Int16(littleEndian: s)) / 32_768
                sum += v * v
            }
            return (sum / Float(samples.count)).squareRoot()
        }
    }
}

@MainActor
final class SpeechPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var isSpeaking = false

    /// Only used on speaker output. Headphones/BT skip this so duplex stays open.
    private let suppressMic = AtomicBool(false)
    /// True while local God audio is playing (even on headphones where mic is not ducked).
    private let playbackActive = AtomicBool(false)
    private var speakingWatchdog: Task<Void, Never>?
    /// Wall-clock time the last scheduled PCM buffer finishes playing; audio can arrive
    /// faster than realtime, so arrival time says nothing about when playback ends.
    private var playbackEndsAt = Date.distantPast

    private var player: AVAudioPlayer?
    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    private var pcmFormat: AVAudioFormat?

    /// Required for `@StateObject` under Swift 6 / macOS 26 — MainActor-isolated init crashes body eval.
    nonisolated override init() {
        super.init()
    }

    private let bargeIn = BargeInGate()

    nonisolated func shouldSuppressMic() -> Bool {
        suppressMic.get()
    }

    /// Mic chunk to send to the worker: unchanged unless God is audible on laptop speakers,
    /// then silence except when the operator talks over God.
    nonisolated func micChunkToSend(_ chunk: Data) -> Data {
        suppressMic.get() ? bargeIn.filter(chunk) : chunk
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
            scheduleEndOfSpeech(after: player.duration)
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
        var sumSquares: Float = 0
        data.withUnsafeBytes { raw in
            guard let src = raw.bindMemory(to: Int16.self).baseAddress,
                  let dst = buffer.floatChannelData?[0] else { return }
            for i in 0..<frameCount {
                let sample = Float(Int16(littleEndian: src[i])) / 32_768
                dst[i] = sample
                sumSquares += sample * sample
            }
        }

        let now = Date()
        let startsAt = max(now, playbackEndsAt)
        playbackEndsAt = startsAt.addingTimeInterval(Double(frameCount) / sampleRate)
        playerNode.scheduleBuffer(buffer, completionHandler: nil)
        if !playerNode.isPlaying {
            playerNode.play()
        }
        // GPT-Live streams output audio nonstop, mostly digital silence. Only audible chunks
        // count as God speaking; otherwise the mic stays ducked forever and Live hears nothing.
        let rms = (sumSquares / Float(frameCount)).squareRoot()
        guard rms >= Self.audibleRMS else { return }
        if !isSpeaking { markSpeaking() }
        scheduleEndOfSpeech(after: playbackEndsAt.timeIntervalSince(now))
    }

    private static let audibleRMS: Float = 0.01

    /// Drop queued audio but keep the engine alive — tearing it down on every barge-in
    /// reconfigures CoreAudio and makes the next reply start late or not at all.
    func stop() {
        speakingWatchdog?.cancel()
        speakingWatchdog = nil
        player?.stop()
        playerNode?.stop()
        playbackEndsAt = .distantPast
        forceMicOpen()
    }

    private func markSpeaking() {
        isSpeaking = true
        playbackActive.set(true)
        // Headphones/BT: no speaker bleed — keep mic open so operator can barge in / reply.
        suppressMic.set(OutputRoute.shouldDuckMicForEcho())
    }

    /// Clears "speaking" once queued audio has actually played, plus a short echo tail
    /// so the room reverb of God's last word is not sent back as operator speech.
    private func scheduleEndOfSpeech(after seconds: TimeInterval) {
        speakingWatchdog?.cancel()
        let delay = max(0, seconds) + 0.25
        speakingWatchdog = Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self.forceMicOpen()
        }
    }

    private func forceMicOpen() {
        isSpeaking = false
        playbackActive.set(false)
        suppressMic.set(false)
    }

    private func ensurePCMEngine(sampleRate: Double) {
        if let engine, pcmFormat?.sampleRate == sampleRate {
            // Output route changes (AirPods on/off) stop the engine; restart instead of rebuilding.
            if !engine.isRunning { try? engine.start() }
            return
        }
        stopPCM()
        // Standard (Float32, deinterleaved) is the format AVAudioPlayerNode is guaranteed to render.
        guard let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1) else { return }

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
        playbackEndsAt = .distantPast
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.scheduleEndOfSpeech(after: 0)
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
