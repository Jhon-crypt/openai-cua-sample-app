import SwiftUI

@main
struct GodApp: App {
    var body: some Scene {
        WindowGroup("God") {
            ContentView()
                .frame(minWidth: 420, minHeight: 520)
        }
        .commands {
            CommandMenu("Session") {
                Button("Stop") {
                    NotificationCenter.default.post(name: .godStop, object: nil)
                }
                .keyboardShortcut(".", modifiers: [.command, .shift])
            }
        }
    }
}

extension Notification.Name {
    static let godStop = Notification.Name("god.stop")
}
