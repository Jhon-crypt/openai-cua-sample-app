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

In the HUD: **Start Session** → **Mic** (auto-runs) or type a **Goal** → **Run Goal**.

You’ll see a **red cursor ring**, hear the agent **talk back** (TTS), and a **token / $ meter** (rates via `GOD_INPUT_USD_PER_MTK`, `GOD_OUTPUT_USD_PER_MTK`, `GOD_WHISPER_USD_PER_MIN`).

**Stop** / ⌘⇧. kills the agent and releases input. Grant Accessibility, Screen Recording, and Microphone.

Docs: [SPEC.md](SPEC.md) · [CAPABILITY-MAP.md](CAPABILITY-MAP.md) · [SPEC-agent-loop.md](SPEC-agent-loop.md) · [SPEC-voice-session.md](SPEC-voice-session.md)
