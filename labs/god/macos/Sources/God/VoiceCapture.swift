@preconcurrency import AVFoundation
import Foundation

enum VoiceCaptureError: LocalizedError {
    case permissionDenied
    case recorderUnavailable
    case noRecording
    case tooShort
    case engineUnavailable

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Microphone permission denied. Enable it in System Settings → Privacy & Security → Microphone."
        case .recorderUnavailable:
            return "Could not start the audio recorder."
        case .noRecording:
            return "No recording is available."
        case .tooShort:
            return "Recording too short. Speak for at least one second, then tap Stop Mic."
        case .engineUnavailable:
            return "Could not start the realtime audio engine."
        }
    }
}

@MainActor
final class VoiceCapture: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var isStreaming = false

    /// Called off the main actor with raw pcm16 mono @ 24 kHz chunks.
    var onPCMChunk: ((Data) -> Void)?

    private var recorder: AVAudioRecorder?
    private var fileURL: URL?
    private var startedAt: Date?

    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private let targetSampleRate: Double = 24_000

    // MARK: - Legacy short WAV capture (Whisper / Run Goal path)

    func start() async throws {
        let granted = await requestPermission()
        guard granted else { throw VoiceCaptureError.permissionDenied }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("god-voice-\(UUID().uuidString).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let recorder: AVAudioRecorder
        do {
            recorder = try AVAudioRecorder(url: url, settings: settings)
        } catch {
            throw VoiceCaptureError.recorderUnavailable
        }
        recorder.isMeteringEnabled = true
        recorder.prepareToRecord()
        guard recorder.record() else { throw VoiceCaptureError.recorderUnavailable }
        self.recorder = recorder
        self.fileURL = url
        self.startedAt = Date()
        isRecording = true
    }

    func stop() throws -> Data {
        guard let recorder, let fileURL else { throw VoiceCaptureError.noRecording }
        let elapsed = Date().timeIntervalSince(startedAt ?? Date())
        recorder.stop()
        isRecording = false
        self.recorder = nil
        self.startedAt = nil

        Thread.sleep(forTimeInterval: 0.15)

        let data = try Data(contentsOf: fileURL)
        try? FileManager.default.removeItem(at: fileURL)
        self.fileURL = nil

        if elapsed < 0.8 || data.count < 2_000 {
            throw VoiceCaptureError.tooShort
        }
        return data
    }

    // MARK: - Realtime PCM stream (24 kHz mono pcm16)

    func startStreaming() async throws {
        if isStreaming { return }
        let granted = await requestPermission()
        guard granted else { throw VoiceCaptureError.permissionDenied }

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.inputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw VoiceCaptureError.engineUnavailable
        }

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: targetSampleRate,
            channels: 1,
            interleaved: true
        ) else {
            throw VoiceCaptureError.engineUnavailable
        }

        let converter = AVAudioConverter(from: inputFormat, to: targetFormat)
        self.converter = converter
        self.engine = engine

        let bufferSize: AVAudioFrameCount = 2_400 // ~100ms at 24 kHz after convert
        input.installTap(onBus: 0, bufferSize: bufferSize, format: inputFormat) { [weak self] buffer, _ in
            guard let self, let converter = self.converter else { return }
            let ratio = targetFormat.sampleRate / inputFormat.sampleRate
            let outFrames = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
            guard let outBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outFrames) else { return }
            var error: NSError?
            let consumedFlag = ConsumedFlag()
            let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
                if consumedFlag.value {
                    outStatus.pointee = .noDataNow
                    return nil
                }
                consumedFlag.value = true
                outStatus.pointee = .haveData
                return buffer
            }
            converter.convert(to: outBuffer, error: &error, withInputFrom: inputBlock)
            guard error == nil, outBuffer.frameLength > 0,
                  let channels = outBuffer.int16ChannelData else { return }
            let byteCount = Int(outBuffer.frameLength) * MemoryLayout<Int16>.size
            let data = Data(bytes: channels[0], count: byteCount)
            self.onPCMChunk?(data)
        }

        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            self.engine = nil
            self.converter = nil
            throw VoiceCaptureError.engineUnavailable
        }
        isStreaming = true
    }

    func stopStreaming() {
        guard isStreaming else { return }
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        converter = nil
        isStreaming = false
    }

    private func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
    }
}

/// Tiny flag box so AVAudioConverterInputBlock can mutate without capturing a var.
private final class ConsumedFlag: @unchecked Sendable {
    var value = false
}
