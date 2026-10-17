import Foundation

/// Drops quiet / noise-floor PCM so Realtime VAD does not treat Mac background noise as speech.
/// While God is playing audio, requires a longer loud streak before opening (real barge-in).
final class MicEnergyGate: @unchecked Sendable {
    private let lock = NSLock()
    private var isOpen = false
    private var loudMs: Double = 0
    private var quietMs: Double = 0
    private var lastOpenAt = Date.distantPast

    /// Normalized RMS (0…1) to start a turn when God is idle.
    private let openThreshold: Float = 0.022
    /// Stay in a turn with a slightly lower floor (avoids chopping words).
    private let holdThreshold: Float = 0.012
    /// Higher bar while God is speaking — brief blips must not barge in.
    private let bargeThreshold: Float = 0.038
    private let openNeedMs: Double = 140
    private let bargeNeedMs: Double = 320
    private let closeNeedMs: Double = 450
    private let sampleRate: Double = 24_000

    func allow(_ data: Data, godSpeaking: Bool) -> Bool {
        let rms = Self.rmsPCM16(data)
        let chunkMs = Double(data.count / 2) / sampleRate * 1_000.0
        guard chunkMs > 0 else { return false }

        lock.lock()
        defer { lock.unlock() }

        let openBar = godSpeaking ? bargeNeedMs : openNeedMs
        let thresh: Float
        if isOpen {
            thresh = holdThreshold
        } else if godSpeaking {
            thresh = bargeThreshold
        } else {
            thresh = openThreshold
        }

        if rms >= thresh {
            loudMs += chunkMs
            quietMs = 0
            if !isOpen, loudMs >= openBar {
                isOpen = true
                lastOpenAt = Date()
            }
        } else {
            quietMs += chunkMs
            loudMs = 0
            if isOpen, quietMs >= closeNeedMs {
                isOpen = false
            }
        }

        if isOpen {
            lastOpenAt = Date()
        }
        return isOpen
    }

    /// True if the gate recently let speech through — used to ignore noise-driven interrupts.
    func wasOpenRecently(withinMs: Double = 600) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return Date().timeIntervalSince(lastOpenAt) * 1_000.0 <= withinMs
    }

    func reset() {
        lock.lock()
        isOpen = false
        loudMs = 0
        quietMs = 0
        lock.unlock()
    }

    private static func rmsPCM16(_ data: Data) -> Float {
        let count = data.count / MemoryLayout<Int16>.size
        guard count > 0 else { return 0 }
        var sum: Float = 0
        data.withUnsafeBytes { raw in
            guard let base = raw.bindMemory(to: Int16.self).baseAddress else { return }
            for i in 0..<count {
                let s = Float(base[i]) / 32768.0
                sum += s * s
            }
        }
        return sqrt(sum / Float(count))
    }
}
