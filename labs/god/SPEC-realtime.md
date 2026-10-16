# Spec: realtime agent

## Objective

God converses with full-duplex voice via the OpenAI Realtime API, operates **any** macOS app with structured GUI tools (visible mouse), keeps a plan + compacted tool context, pauses on human mouse move, and stops when stuck repeating failures.

## Approach

Harness components (inspired by Fan et al. arXiv:2609.20804):

| Component | Implementation |
|-----------|----------------|
| Action space | `open_app`, `screenshot`, `click`, `type_text`, `hotkey`, `update_plan`, plus `exec_py` escape |
| Planning | `update_plan` + plan injected into session instructions |
| Context | Local T4-style elision of bulky tool results + running summary injected into instructions |
| Verify | Auto-screenshot after open/click/type/hotkey; attach compact PNG to conversation when possible |
| Stuck | Identical failing tool streaks → remind, then freeze actions |
| Mouse | Slow visible moves + HUD red ring; human move → spoken pause |

- Worker owns WebSocket Realtime (`gpt-realtime-2.1`, override `GOD_REALTIME_MODEL`).
- HUD streams 24 kHz PCM mic; plays 24 kHz PCM agent audio (mic upload paused while God speaks).

## Protocol

```json
{"type":"realtime_start","id":"1","policy":"..."}
{"type":"realtime_audio_in","id":"2","audio_base64":"..."}
{"type":"realtime_text","id":"3","text":"..."}
{"type":"realtime_stop","id":"4"}
```

## Success Criteria

- Start Session opens Realtime; God greets by voice
- Structured tools can open any named app and click/type with a visible cursor
- Multi-step goals use `update_plan`
- Human mouse move triggers spoken pause + continue
- Repeated identical failures trigger stuck reminder then freeze
- Stop / ⌘⇧. tears down the socket and releases input

## Sources

- https://platform.openai.com/docs/guides/realtime-conversations
- https://arxiv.org/abs/2609.20804
