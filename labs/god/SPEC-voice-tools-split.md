# Spec: voice-tools-split

## Objective

Keep the Realtime mic **always listen-capable** during long desktop work. Voice stays on Realtime; click/browse chains run on a **separate background agent loop** so tool execution does not block turn-taking.

## Why

OpenAI Realtime cannot usefully process new user turns while the same session is blocked inside long tool batches. Mid-browse “deafness” is an architecture limit, not a VAD bug.

## Approach

| Layer | Role |
|-------|------|
| Realtime | Duplex voice, transcripts, short acknowledgements |
| Voice tools | `run_desktop_task`, `cancel_desktop_task`, `get_desktop_status` only |
| DesktopTaskRunner | Background thread → Responses `agent_loop` (`exec_py` / desktop) |
| Completion | Runner posts a SYSTEM note into Realtime → God speaks the result |

Realtime **must not** expose inline `open_app` / `click_*` / `type_text` / `hotkey` / `screenshot` / `exec_py` — those block the voice loop.

## Protocol (Realtime function tools)

```json
{"name":"run_desktop_task","arguments":{"goal":"Open Safari and go to example.com"}}
{"name":"cancel_desktop_task","arguments":{}}
{"name":"get_desktop_status","arguments":{}}
```

`run_desktop_task` returns immediately (`started: true`). Progress emits `agent_event`. On finish/cancel/error, worker injects a user SYSTEM message and `response.create` so God narrates.

## Success Criteria

- While a desktop task runs, mic audio still appends and Realtime can answer / cancel
- Multi-step browse goals use `run_desktop_task`, not inline GUI tools on Realtime
- Mouse pause only applies when a desktop task is active (or cooldown after one)
- Cancel stops the background loop and God acknowledges
- Existing noise-ignore + manual `response.create` behavior unchanged

## Boundaries

- Always: one desktop task at a time; new `run_desktop_task` while busy → reject or cancel-then-restart only if operator asked
- Never: block the Realtime event thread on long `exec_py` / GUI work
