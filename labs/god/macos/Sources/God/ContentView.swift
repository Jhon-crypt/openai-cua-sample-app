import SwiftUI

struct ContentView: View {
    @StateObject private var worker = WorkerClient()
    @State private var session: SessionPhase = .idle
    @State private var log: [String] = ["God slice 1 — worker bridge + kill switch."]
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
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

            Text(worker.statusText)
                .font(.callout)
                .foregroundStyle(.secondary)

            HStack(spacing: 12) {
                Button("Start Session") { Task { await startSession() } }
                    .disabled(busy || session == .listening || session == .acting)
                Button("Test Click") { Task { await testClick() } }
                    .disabled(busy || !worker.ready || session == .stopped || session == .idle)
                Button("Stop", role: .destructive) { Task { await stopSession() } }
                    .disabled(busy || session == .idle || session == .stopped)
                    .keyboardShortcut(".", modifiers: [.command, .shift])
            }

            Text("Kill switch: ⌘⇧. — releases input even if the agent misbehaves.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(log.enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
        .padding(20)
        .onReceive(NotificationCenter.default.publisher(for: .godStop)) { _ in
            Task { await stopSession() }
        }
    }

    private var statusColor: Color {
        switch session {
        case .idle: return .gray
        case .listening: return .green
        case .acting: return .orange
        case .stopped: return .red
        }
    }

    private func append(_ line: String) {
        log.append(line)
    }

    private func startSession() async {
        busy = true
        defer { busy = false }
        append("Starting session…")
        do {
            try await worker.start()
            session = .listening
            append("Session open — worker ready.")
        } catch {
            session = .idle
            append("Start failed: \(error.localizedDescription)")
        }
    }

    private func testClick() async {
        busy = true
        defer { busy = false }
        session = .acting
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
        do {
            if worker.ready {
                let response = try await worker.send(type: "stop")
                let detail = (response["detail"] as? String) ?? "released"
                append("stop → \(detail)")
            }
        } catch {
            append("stop send: \(error.localizedDescription)")
        }
        worker.stopWorker()
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
