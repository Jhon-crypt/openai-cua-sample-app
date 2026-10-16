import AppKit
import SwiftUI

@main
struct GodApp: App {
    @NSApplicationDelegateAdaptor(GodAppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup("God") {
            ContentView()
                .frame(minWidth: 420, minHeight: 520)
        }
        .defaultSize(width: 480, height: 560)
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

final class GodAppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // SPM executables default to a faceless policy — force a normal app so the window shows.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows {
            window.makeKeyAndOrderFront(nil)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

extension Notification.Name {
    static let godStop = Notification.Name("god.stop")
}
