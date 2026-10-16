import AVFoundation
import Foundation

enum VoiceCaptureError: LocalizedError {
    case permissionDenied
    case recorderUnavailable
    case noRecording

    var errorDescription: String? {
        switch self {
        case .permissionDenied: return "Microphone permission denied."
        case .recorderUnavailable: return "Could not start the audio recorder."
        case .noRecording: return "No recording is available."
        }
    }
}

@MainActor
final class VoiceCapture: ObservableObject {
    @Published private(set) var isRecording = false

    private var recorder: AVAudioRecorder?
    private var fileURL: URL?

    func start() async throws {
        let granted = await requestPermission()
        guard granted else { throw VoiceCaptureError.permissionDenied }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("god-voice-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        guard let recorder = try? AVAudioRecorder(url: url, settings: settings) else {
            throw VoiceCaptureError.recorderUnavailable
        }
        recorder.prepareToRecord()
        guard recorder.record() else { throw VoiceCaptureError.recorderUnavailable }
        self.recorder = recorder
        self.fileURL = url
        isRecording = true
    }

    func stop() throws -> Data {
        guard let recorder, let fileURL else { throw VoiceCaptureError.noRecording }
        recorder.stop()
        isRecording = false
        self.recorder = nil
        let data = try Data(contentsOf: fileURL)
        try? FileManager.default.removeItem(at: fileURL)
        self.fileURL = nil
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
