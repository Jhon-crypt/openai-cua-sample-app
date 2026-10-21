# Spec: realtime agent

> **Status:** fallback only (`GOD_VOICE_BACKEND=realtime`). The default voice backend is GPT-Live ([SPEC-live.md](SPEC-live.md)).

## Objective

God converses with full-duplex voice via the OpenAI Realtime API, hands **desktop control** to a background agent loop (so the mic stays usable mid-browse), pauses desktop work on human mouse move while a task is active, and narrates results when the desktop runner finishes.

## Approach

Harness components (inspired by Fan et al. arXiv:2609.20804), split across voice vs desktop:

| Component | Implementation |
|-----------|----------------|
| Voice action space | `run_desktop_task`, `cancel_desktop_task`, `get_desktop_status` (Realtime) |
| Desktop action space | Responses `agent_loop` + `exec_py` / GUI tools (background thread) |
| Coordinate map | Desktop path: screenshot pixel space → harness / pyautogui |
| Planning / context / stuck | Desktop / agent-loop harness (not blocking Realtime) |
| Mouse | Slow visible moves + HUD red ring; human move → pause **only while desktop task active** |

- Worker owns WebSocket Realtime (`gpt-realtime-2.1`, override `GOD_REALTIME_MODEL`).
- HUD streams 24 kHz PCM mic; plays 24 kHz PCM agent audio (speaker echo duck only).
- Audio profile is device-aware: laptop → `far_field` + `server_vad` (threshold ≈ 0.58, `create_response=false`); headset → `near_field` + `semantic_vad`. Client calls `response.create` only after a non-noise transcript. Hot-swap via `realtime_audio_profile`.
- See [SPEC-voice-tools-split.md](SPEC-voice-tools-split.md).

## Protocol

```json
{"type":"realtime_start","id":"1","policy":"...","noise_reduction":"far_field"}
{"type":"realtime_audio_in","id":"2","audio_base64":"..."}
{"type":"realtime_text","id":"3","text":"..."}
{"type":"realtime_audio_profile","id":"4","noise_reduction":"near_field"}
{"type":"realtime_stop","id":"5"}
```

## Success Criteria

- Start Session opens Realtime; God greets by voice
- Desktop goals use `run_desktop_task`; Realtime stays responsive while the runner works
- Human mouse move during an active desktop task triggers spoken pause + continue
- Cancel / Stop / ⌘⇧. tears down Realtime, cancels desktop runner, releases input

## Sources

- https://platform.openai.com/docs/guides/realtime-conversations
- https://arxiv.org/abs/2609.20804
