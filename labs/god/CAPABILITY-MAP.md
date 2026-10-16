# Capability Map: God (Voice Mac Operator)

| Module id | Responsibility | Depends on |
|-----------|----------------|------------|
| shell-hud | SwiftUI window / menu-bar presence, session UI, transcript pane, kill-switch hotkey | — |
| desktop-worker | Python process: screenshots, mouse/keyboard, input release on stop | — |
| agent-loop | Model turns, tool calls (`exec_py`-style), spoken policy in context (Responses fallback) | desktop-worker, shell-hud |
| realtime | OpenAI Realtime duplex voice + `exec_py` + session memory + mouse-interrupt pause | desktop-worker, shell-hud |
| voice-session | Continuous 24 kHz mic stream into Realtime; PCM playback | shell-hud, realtime |
| session-log | Append-only event log for a session (local files) | shell-hud |

**Build order:** `shell-hud` + `desktop-worker` → `agent-loop` → `realtime` / `voice-session` → `session-log`

**Index:** module specs live as `SPEC-<module-id>.md` in this folder.
