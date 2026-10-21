# Spec: God (MVP)

> **Status:** MVP shipped. This file is the original product spec; current architecture, protocol and pitfalls are in [AGENTS.md](AGENTS.md), voice wiring in [SPEC-live.md](SPEC-live.md).

## Objective

Personal native Mac co-pilot: I open a session, speak goals and do’s/don’ts, and a Python worker drives the desktop while a Swift HUD shows transcript, narration, and a kill switch.

**User:** only me.  
**Success (MVP):** From the God window I can start a worker session, trigger a test click on the primary display, and stop/release input within ~500ms via UI or ⌘⇧. — before voice or model loop exist.

## Tech Stack

- macOS 14+, SwiftUI (app under `macos/`), built with Swift ≥ 6.3.3
- Python 3.10+ worker under `worker/` (PyAutoGUI; same permission model as `python-app`)
- OpenAI: GPT-Live (`gpt-live-1`) for voice, Responses API for the desktop agent, Realtime API as fallback voice
- JSON-lines protocol over the worker’s stdin/stdout (reference in [AGENTS.md](AGENTS.md))

## Commands

```bash
# Worker check (from repo root)
uv run --project labs/god/worker python -m god_worker --check

# Run worker (stdio JSON-lines)
uv run --project labs/god/worker python -m god_worker

# Build / run HUD
cd labs/god/macos && swift build && swift run God

# Worker unit tests
uv run --project labs/god/worker pytest
```

## Project Structure

```text
labs/god/
  AGENTS.md              → start here (architecture, protocol, pitfalls, verify)
  CAPABILITY-MAP.md      → module index; SPEC-<module>.md per module
  SPEC.md
  ideas/voice-mac-operator.md
  tasks/plan.md
  tasks/todo.md
  macos/                 → SwiftUI HUD (shell-hud)
  worker/                → Python package god_worker (desktop-worker, live, agent-loop, realtime)
  worker/scripts/        → live_probe.py (talk to GPT-Live without a mic)
```

## Code Style

- Swift: SwiftUI, clear session state enum, no force-unwraps in control paths
- Python: typed functions, JSON-lines request/response, never block stop on model I/O
- Protocol messages use `type` + snake_case fields

## Testing Strategy

- Worker: pytest for protocol encode/decode and stop/release helpers (mock pyautogui where needed)
- HUD: `swift build` as gate for slice 1; UI tests later
- Manual: Accessibility + Screen Recording granted; Test Click moves cursor; Stop releases

## Boundaries

- **Always:** keep God out of `labs/catalog.json`; kill switch must not depend on the model; commit no secrets
- **Ask first:** wake-phrase STT vendor choice; App Store / notarization; changing stop hotkey defaults
- **Never:** ship without a mechanical stop; auto-buy / password fill in MVP; register as a sample console scenario

## Success Criteria

- [x] `god_worker --check` reports permissions or a clear error
- [x] HUD builds and shows Session idle / listening / acting / stopped
- [x] Test Click performs one primary-display click via the worker
- [x] Stop (button or ⌘⇧.) asks worker to release keys/mouse and marks session stopped
- [x] Specs and capability map committed under `labs/god/`

## Open Questions (defaults for now)

| Question | Default until changed |
|----------|----------------------|
| Wake | Hotkey / Start Session button only |
| Stop hotkey | ⌘⇧. |
| Remember spoken rules | Session only |
| STT / model | GPT-Live (voice) + Responses (desktop agent) |
