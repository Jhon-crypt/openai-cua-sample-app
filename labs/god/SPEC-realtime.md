# Spec: realtime agent

## Objective

God converses with full-duplex voice via the OpenAI Realtime API, performs desktop tasks mid-conversation (`exec_py`), keeps session memory, and pauses with a spoken prompt when the operator moves the mouse.

## Approach

- Worker owns a WebSocket Realtime session (`gpt-realtime-2.1` by default, override `GOD_REALTIME_MODEL`).
- HUD streams 24 kHz PCM mic audio; plays 24 kHz PCM agent audio.
- Tools: same `exec_py` surface as the Responses loop.
- Memory: Realtime conversation items (server-side for the session lifetime).
- Mouse interrupt: poll cursor; if movement is not from our PyAutoGUI moves, inject a system notice and ask the operator to say when to continue.

## Protocol

```json
{"type":"realtime_start","id":"1","policy":"..."}
{"type":"realtime_audio_in","id":"2","audio_base64":"..."}
{"type":"realtime_text","id":"3","text":"..."}
{"type":"realtime_stop","id":"4"}
```

Events:

```json
{"type":"speak","audio_base64":"...","format":"pcm16","sample_rate":24000,"stream":true}
{"type":"realtime_interrupt","reason":"mouse"|"speech"}
{"type":"agent_event","level":"info","message":"..."}
{"type":"usage", ...}
```

## Success Criteria

- Start Session opens Realtime; God greets by voice
- Operator can talk continuously; God replies by voice and can run `exec_py`
- Moving the mouse (by the human) triggers a spoken pause + continue prompt
- Conversation context persists for the open session
- Stop / ⌘⇧. tears down the Realtime socket and releases input

## Sources

- https://platform.openai.com/docs/guides/realtime-conversations
