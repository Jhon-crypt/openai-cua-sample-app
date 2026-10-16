# God

Personal **voice Mac operator** workspace. Not a browser lab template — do **not** register in `catalog.json`.

## Run

Requires root `.env` with `OPENAI_API_KEY`. Harness = this repo’s Responses API + `exec_py` (not Agents SDK ComputerTool).

```bash
uv sync --project labs/god/worker --extra dev
uv run --project labs/god/worker python -m god_worker --check
uv run --project labs/god/worker pytest

cd labs/god/macos && swift run God
```

In the HUD: **Start Session** → type a **Goal** (or **Mic** → Stop Mic) → **Run Goal**. **Stop** / ⌘⇧. kills the agent and releases input.

Grant Accessibility, Screen Recording, and Microphone to Terminal / God.

Docs: [SPEC.md](SPEC.md) · [CAPABILITY-MAP.md](CAPABILITY-MAP.md) · [SPEC-agent-loop.md](SPEC-agent-loop.md) · [SPEC-voice-session.md](SPEC-voice-session.md)
