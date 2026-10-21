# Spec: shell-hud

> **Status:** slice-1 spec, shipped. The HUD now also streams the mic and plays Live audio; see [AGENTS.md](AGENTS.md) for the current protocol and audio rules.

## Objective

Native SwiftUI app that owns the session chrome: status, Start/Stop, Test Click, live log lines, kill-switch hotkey.

## Success Criteria

- App launches as `God` on macOS
- Shows connection state to the worker (disconnected / ready / error)
- Start Session → idle listening placeholder (mic wiring later)
- Test Click → sends `{ "type": "click_center" }` to worker, appends result to log
- Stop / ⌘⇧. → sends `{ "type": "stop" }`, UI shows stopped

## Boundaries

- Always: stop works even if worker is busy
- Never: call OpenAI from the HUD in slice 1
