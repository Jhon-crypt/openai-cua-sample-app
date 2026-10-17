import AppKit
import SwiftUI

/// Borderless always-on-top ring that follows agent mouse targets.
@MainActor
final class CursorOverlayController: ObservableObject {
    @Published var point: CGPoint?
    private var window: NSWindow?
    private var hosting: NSHostingView<CursorRingView>?

    /// Required for `@StateObject` under Swift 6 / macOS 26 — MainActor-isolated init crashes body eval.
    nonisolated init() {}

    func show() {
        if window != nil { return }
        let view = CursorRingView(controller: self)
        let hosting = NSHostingView(rootView: view)
        let window = NSWindow(
            contentRect: NSScreen.main?.frame ?? .init(x: 0, y: 0, width: 800, height: 600),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.contentView = hosting
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.level = .screenSaver
        window.ignoresMouseEvents = true
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.setFrame(NSScreen.main?.frame ?? window.frame, display: true)
        window.orderFrontRegardless()
        self.window = window
        self.hosting = hosting
    }

    func hide() {
        window?.orderOut(nil)
        window = nil
        hosting = nil
        point = nil
    }

    func move(toX x: Int, y: Int) {
        show()
        // PyAutoGUI uses top-left origin; AppKit uses bottom-left.
        let screen = NSScreen.main ?? NSScreen.screens[0]
        let height = screen.frame.height
        let flipped = CGPoint(x: CGFloat(x), y: height - CGFloat(y))
        withAnimation(.easeOut(duration: 0.2)) {
            point = flipped
        }
    }
}

private struct CursorRingView: View {
    @ObservedObject var controller: CursorOverlayController

    var body: some View {
        GeometryReader { _ in
            ZStack {
                Color.clear
                if let point = controller.point {
                    Circle()
                        .strokeBorder(Color.red.opacity(0.95), lineWidth: 3)
                        .background(Circle().fill(Color.red.opacity(0.2)))
                        .frame(width: 36, height: 36)
                        .position(point)
                    Circle()
                        .fill(Color.red)
                        .frame(width: 6, height: 6)
                        .position(point)
                }
            }
        }
        .allowsHitTesting(false)
    }
}
