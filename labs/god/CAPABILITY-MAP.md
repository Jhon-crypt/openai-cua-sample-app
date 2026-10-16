# Capability Map: God (Voice Mac Operator)

| Module id | Responsibility | Depends on |
|-----------|----------------|------------|
| shell-hud | SwiftUI window / menu-bar presence, session UI, transcript pane, kill-switch hotkey | — |
| desktop-worker | Python process: screenshots, mouse/keyboard, input release on stop | — |
| agent-loop | Model turns, tool calls (`exec_py`-style), spoken policy in context (Responses fallback) | desktop-worker, shell-hud |
| realtime | OpenAI Realtime duplex voice + GUI tools + plan/context/stuck + mouse-interrupt | desktop-worker, shell-hud |
| gui-tools | Structured GUI tools + scaled `click` + `click_text` (Vision OCR) + `click_element` (AX) | desktop-worker |
| grounding | Screenshot↔screen mapping, OCR, Accessibility hit-testing | gui-tools |
| harness-plan | Persistent `update_plan` injected into Realtime instructions | realtime |
| harness-context | T4-style elision + summary of bulky tool results | realtime |
| harness-stuck | Repeated failing-tool streaks → remind / freeze | realtime |
| voice-session | Continuous 24 kHz mic stream into Realtime; PCM playback | shell-hud, realtime |
| session-log | Append-only event log for a session (local files) | shell-hud |

**Build order:** `shell-hud` + `desktop-worker` → `gui-tools` / harness-* → `realtime` / `voice-session` → `session-log`

**Index:** module specs live as `SPEC-<module-id>.md` in this folder.
