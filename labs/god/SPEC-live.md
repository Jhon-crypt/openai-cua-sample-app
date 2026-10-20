# Spec: live (GPT-Live voice frontend)

## Objective

Replace the Realtime voice loop with **GPT-Live** (`gpt-live-1`, `wss://api.openai.com/v1/live/sessions`) so God:

- only speaks when addressed (no replies to TV / room chatter / coughs),
- keeps listening while desktop work runs (full duplex),
- hands every Mac action to the local `DesktopTaskRunner` via **client delegation**,
- never claims an action happened before the runner confirms it.

## Why Live fixes the Realtime problems

| Problem on Realtime | Cause | Live mechanism |
|---|---|---|
| Replies to background noise | Our code calls `response.create` on every non-noise transcript | No VAD / `response.create` — the model owns turn-taking; prompt sections *Selected requests only* + *Silence and background noise* |
| Too forward / talks unprompted | SYSTEM notices forced `response.create` | `session.thinking.append` = silent context; `session.commentary.append` only for results worth saying |
| Ignores short commands after mouse pause | `should_resume_from_transcript` needs ≥4 words | No pause gate — every delegation is a fresh request |
| Deaf while browsing | Tool calls blocked the voice session | Delegation is async; voice keeps listening |
| VAD threshold / NR tuning | Realtime-only knobs | Gone; Live has no turn-detection config |

## Wiring (client delegation)

| Live event | God action |
|---|---|
| send `session.start` (model, instructions, `audio.format` pcm 24k, `audio.output.voice`, `delegation:{type:"client"}`) | first message; wait for `session.started` before audio |
| send `session.input_audio.append` | every HUD mic chunk |
| `session.output_audio.delta` | `speak` pcm16 → HUD |
| `session.input_transcript.delta` / `session.output_transcript.delta` | buffer by speaker with `start_ms`; flush to HUD log on idle / speaker switch |
| `session.delegation.created` | wait ~0.8 s for transcript, build goal from user text since last delegation; cancel intent → cancel runner; else start / replace runner task keyed by `delegation.id` |
| runner progress | `session.thinking.append` (silent) with `delegation_id` |
| runner result | `session.commentary.append` with `delegation_id`; stale (superseded) results dropped |
| operator moves mouse during task | cancel runner; on confirmed cancel → short commentary |
| typed text | start runner directly; mirror with `session.thinking.append` (`delegation_id: null`) |
| `session.usage.updated` | cumulative voice seconds → meter ($0.05/min, `GOD_LIVE_USD_PER_MIN`) |
| `error` | log; keep session unless `session.closed` follows |
| stop | send `session.close`, wait ≤5 s for `session.closed`, then close socket |

## Boundaries

- Always: one desktop task at a time; append content ≤ 500 tokens; app (not voice model) decides cancel/replace.
- Ask first: spoken greeting, push-to-talk via `session.input_audio.mute`.
- Never: claim success before runner result; send audio before `session.started`.

## Success Criteria

- Background TV / chatter for 60 s → God stays silent.
- "Open Chrome" (2 words) → task starts.
- "Stop" mid-task → runner cancelled, God confirms after cancel completes.
- Correction mid-task ("no, Safari") → old task cancelled, its result never spoken.
- Talking while a task runs → God answers; task continues.

## Sources

- https://developers.openai.com/api/docs/guides/live
- https://developers.openai.com/api/docs/guides/live-delegation
- https://developers.openai.com/api/docs/guides/live-prompting
- https://developers.openai.com/api/docs/guides/live-conversations
- https://developers.openai.com/api/docs/guides/voice-websockets
