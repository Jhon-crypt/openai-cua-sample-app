import AVFoundation
import Foundation

enum VoiceCaptureError: LocalizedError {
    case permissionDenied
    case recorderUnavailable
    case noRecording
    case tooShort

    var errorDescription: String? {
        switch self {
        case .permissionDenied: return "Microphone permission denied. Enable it in System Settings → Privacy & Security → Microphone."
        case .recorderUnavailable: return "Could not start the audio recorder."
        case .noRecording: return "No recording is available."
        case .tooShort: return "Recording too short. Speak for at least one second, then tap Stop Mic."
        }
    }
}

@MainActor
final class VoiceCapture: ObservableObject {
    @Published private(set) var isRecording = false

    private var recorder: AVAudioRecorder?
    private var fileURL: URL?
    private var startedAt: Date?

    func start() async throws {
        let granted = await requestPermission()
        guard granted else { throw VoiceCaptureError.permissionDenied }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("god-voice-\(UUID().uuidString).wav")
        // Linear PCM WAV — Whisper handles this more reliably than AAC/m4a from AVAudioRecorder.
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

        // Let the file flush to disk.
        Thread.sleep(forTimeInterval: 0.15)

        let data = try Data(contentsOf: fileURL)
        try? FileManager.default.removeItem(at: fileURL)
        self.fileURL = nil

        if elapsed < 0.8 || data.count < 2_000 {
            throw VoiceCaptureError.tooShort
        }
        return data
    }

    private func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
    }
}
