import AVFoundation
import Foundation

@MainActor
final class SpeechPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var isSpeaking = false
    private var player: AVAudioPlayer?

    func play(base64: String, format: String = "mp3") {
        guard let data = Data(base64Encoded: base64) else { return }
        do {
            player?.stop()
            let player = try AVAudioPlayer(data: data)
            player.delegate = self
            self.player = player
            isSpeaking = true
            player.play()
        } catch {
            isSpeaking = false
        }
    }

    func stop() {
        player?.stop()
        isSpeaking = false
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.isSpeaking = false
        }
    }
}
