# Capability Map: God (Voice Mac Operator)

| Module id | Responsibility | Depends on |
|-----------|----------------|------------|
| shell-hud | SwiftUI window / menu-bar presence, session UI, transcript pane, kill-switch hotkey | — |
| desktop-worker | Python process: screenshots, mouse/keyboard, input release on stop | — |
| agent-loop | Model turns, tool calls (`exec_py`-style), spoken policy in context | desktop-worker, shell-hud |
| voice-session | Open mic after hotkey/wake; keep listening for session; STT → transcript + goals | shell-hud |
| session-log | Append-only event log for a session (local files) | shell-hud |

**Build order:** `shell-hud` + `desktop-worker` (slice 1, parallel) → `agent-loop` → `voice-session` → `session-log`

**Index:** module specs live as `SPEC-<module-id>.md` in this folder.
