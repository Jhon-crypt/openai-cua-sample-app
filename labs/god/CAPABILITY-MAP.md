# Capability Map: God (Voice Mac Operator)

| Module id | Responsibility | Depends on |
|-----------|----------------|------------|
| shell-hud | SwiftUI window / menu-bar presence, session UI, transcript pane, kill-switch hotkey | — |
| desktop-worker | Python process: screenshots, mouse/keyboard, input release on stop | — |
| agent-loop | Model turns, tool calls (`exec_py`-style), spoken policy in context (Responses fallback) | desktop-worker, shell-hud |
| live | GPT-Live voice frontend (default) — client delegation to DesktopTaskRunner | desktop-worker, shell-hud, voice-tools-split |
| realtime | Fallback: OpenAI Realtime duplex voice + voice-control tools (`GOD_VOICE_BACKEND=realtime`) | desktop-worker, shell-hud, voice-tools-split |
| voice-tools-split | Background DesktopTaskRunner; Realtime never blocks on long GUI/exec | realtime, agent-loop |
| gui-tools | Structured GUI tools + scaled `click` + `click_text` (Vision OCR) + `click_element` (AX) | desktop-worker |
| grounding | Screenshot↔screen mapping, OCR, Accessibility hit-testing | gui-tools |
| harness-plan | Persistent `update_plan` (desktop / agent-loop path) | agent-loop, gui-tools |
| harness-context | T4-style elision + summary of bulky tool results | agent-loop |
| harness-stuck | Repeated failing-tool streaks → remind / freeze | agent-loop |
| voice-session | Continuous 24 kHz mic stream into the voice backend; PCM playback + mic ducking (HUD) | shell-hud, live |
| session-log | Append-only event log for a session (local files) | shell-hud |

**Build order:** `shell-hud` + `desktop-worker` → `gui-tools` / harness-* → `agent-loop` → `voice-tools-split` → `realtime` / `voice-session` → `session-log`

**Index:** module specs live as `SPEC-<module-id>.md` in this folder.
