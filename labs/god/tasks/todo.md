# Tasks: God

- [x] Capability map + specs
- [x] Task: desktop-worker package with ping / click_center / stop / --check
  - Acceptance: pytest passes; `--check` runs
  - Verify: `uv run --project labs/god/worker pytest` and `--check`
  - Files: `labs/god/worker/**`
- [x] Task: SwiftUI God HUD spawns worker, Test Click, Stop, ⌘⇧.
  - Acceptance: `swift build` succeeds; manual click + stop
  - Verify: `cd labs/god/macos && swift build`
  - Files: `labs/god/macos/**`
- [x] Task: agent-loop (Responses + exec_py in worker; HUD Run Goal)
- [x] Task: voice-session (Mic → Whisper → goal field)
- [ ] Task: session-log (later)
