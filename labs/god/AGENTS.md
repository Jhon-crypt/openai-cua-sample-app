# God — agent handoff

Personal voice Mac operator: you talk, a voice model answers, a background agent drives the Mac.
Read this first, then [SPEC-live.md](SPEC-live.md) (voice wiring) and [README.md](README.md) (run + env vars).
Backlog: [tasks/todo.md](tasks/todo.md). Not a browser lab — never register in `labs/catalog.json`.

## Architecture

```text
HUD (SwiftUI, macos/)  ──stdin/stdout JSON lines──  worker (Python, worker/)  ──WebSocket──  GPT-Live (gpt-live-1)
  mic 24 kHz pcm16 ─────────────────────────────▶  LiveSession ─────────────────────────▶  hears, decides when to speak
  SpeechPlayer ◀── speak (pcm16) ───────────────  LiveSession ◀── output audio ─────────  delegation.created (no text!)
  log / meter / cursor ring ◀── agent_event, usage, mouse                │
                                                                        ▼
                                                DesktopTaskRunner (one task, own thread)
                                                  agent_loop: Responses + exec_py, ≤20 turns
                                                  result → session.commentary.append (spoken)
```

| Path | Role |
|---|---|
| `macos/Sources/God/ContentView.swift` | Session UI, event routing (`wireEvents`), mic → worker |
| `macos/Sources/God/WorkerClient.swift` | Spawns worker, JSON-lines I/O, `AudioIngress` (off-main stdin writes) |
| `macos/Sources/God/SpeechPlayer.swift` | PCM playback, "God speaking", mic ducking on laptop speakers |
| `macos/Sources/God/VoiceCapture.swift` | Mic tap → 40 ms pcm16 chunks (no echo cancellation) |
| `worker/src/god_worker/__main__.py` | Request handlers; `GOD_VOICE_BACKEND` picks Live (default) or Realtime |
| `worker/src/god_worker/live_session.py` | GPT-Live client: events, transcripts, delegation → runner |
| `worker/src/god_worker/desktop_task.py` | Background runner: start / replace / cancel, stale results dropped |
| `worker/src/god_worker/agent_loop.py` | Desktop agent (Responses + `exec_py`, stuck detection) |
| `worker/src/god_worker/realtime_session.py` | Fallback voice backend (Realtime API + voice-control tools) |

## HUD ↔ worker protocol

One JSON object per line. Requests carry `type` + `id`; the worker answers each with `ack` / `result` / `error` (same `id`). Names say `realtime_*` for history — they drive whichever voice backend is active.

| HUD → worker | Fields | Effect |
|---|---|---|
| `realtime_start` | `policy`, `noise_reduction` (`far_field` = laptop speakers, `near_field` = headphones) | Open voice session |
| `realtime_audio_in` | `audio_base64` (pcm16 24 kHz mono, 40 ms) | Mic chunk; sent continuously |
| `realtime_text` | `text` | Typed goal → desktop runner |
| `realtime_audio_profile` | `noise_reduction` | Output route changed (no-op on Live) |
| `realtime_stop` / `agent_stop` / `stop` | — | Close voice / cancel agent / release all input |
| `click_center`, `ping`, `usage`, `agent_run`, `transcribe` | see `__main__.py` | Diagnostics and legacy paths |

| Worker → HUD (unsolicited) | Fields | HUD action |
|---|---|---|
| `ready` | — | Worker up |
| `speak` | `audio_base64`, `format` (`pcm16`/`mp3`), `sample_rate`, `stream` | `SpeechPlayer.enqueuePCM` / `play` |
| `realtime_interrupt` | `reason` (`speech`, `mouse`, …) | Non-mouse → `speech.stop()` (flush queued audio) |
| `agent_event` | `level` (`info`/`ok`/`pending`/`error`), `message` | Log line |
| `usage` | meter snapshot | Cost bar |
| `mouse` | `x`, `y` | Red cursor ring |

## Invariants (don't break)

- The kill switch (Stop, ⌘⇧.) never depends on the model.
- Never send audio to Live before `session.started`; never claim a Mac action happened before the runner reports it.
- One desktop task at a time; a correction replaces the task and the old result is never spoken.
- Live appends (`thinking` / `commentary` / `instructions`) stay ≤ 500 tokens.
- Mic chunks keep flowing during a session: when ducked, send **silence of the same length**, not nothing.

## Pitfalls we already hit

- **GPT-Live streams output audio nonstop, mostly digital silence** (~13 s of audio per 17 s, < 0.5 s audible). Anything that treats "audio arrived" as "God is talking" mutes the mic forever and Live never hears you. `SpeechPlayer` only counts chunks with RMS ≥ 0.01 as speech. The worker's `_maybe_flush_playback` barge-in heuristic assumes bursty audio and effectively never fires on Live.
- **Live timestamps (`start_ms`, `offset_ms`) are positions in the input stream**, so dropped mic chunks skew them.
- **`session.delegation.created` has no task text.** The goal is the user's transcript in the 12 s before `offset_ms`, read 0.8 s after the event (transcripts trail).
- **Laptop speakers have no echo cancellation**: the mic is ducked while God is audible. Headphones keep it open.
- **Swift 6.3.2 / Xcode 26.5 crashes the HUD mid-session** in `swift_task_isCurrentExecutor…` / `ContentView.body` — a compiler bug ([swiftlang/swift#89197](https://github.com/swiftlang/swift/issues/89197)), not app code. Build with Swift ≥ 6.3.3 (see README). Don't "fix" it by restructuring views.
- The `nonisolated init` on `@MainActor` classes (`SpeechPlayer`, `WorkerClient`, `VoiceCapture`, `CursorOverlay`) and the nonisolated mic tap install are deliberate; keep them.
- Debug from evidence: crash reports are in `~/Library/Logs/DiagnosticReports/God-*.ips`; for voice behavior run the Live probe before changing code.

## Verify a change

```bash
uv run --project labs/god/worker pytest                      # worker unit tests
export TOOLCHAINS=$(plutil -extract CFBundleIdentifier raw ~/Library/Developer/Toolchains/swift-6.3.3-RELEASE.xctoolchain/Info.plist)
cd labs/god/macos && swift build && swift run God             # HUD (needs Accessibility, Screen Recording, Mic)
uv run --project labs/god/worker python labs/god/worker/scripts/live_probe.py "Hey God, open Chrome"
```

The probe talks to Live with a synthetic voice (no mic) and prints transcripts, delegations and how much output audio was audible. Expected for "Hey God, open Chrome": input transcript deltas, one `session.delegation.created`, a short reply ("On it."). It costs a few cents.

Manual checks that still need a human are listed under *Success Criteria* in [SPEC-live.md](SPEC-live.md).

## Working rules

- Commit and push after each piece of work.
- Ask first: spoken greeting, push-to-talk / idle auto-close, changing the stop hotkey, notarization.
- Specs are `SPEC-<module>.md`, indexed in [CAPABILITY-MAP.md](CAPABILITY-MAP.md); update the relevant one when behavior changes.
