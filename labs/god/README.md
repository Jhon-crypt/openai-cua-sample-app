# God

Personal **voice Mac operator** workspace. Not a browser lab template — do **not** register in `catalog.json`.

## Run

Requires root `.env` with `OPENAI_API_KEY`. Primary harness = **OpenAI Realtime API** (duplex voice + `exec_py`). Optional fallback: Responses loop.

```bash
uv sync --project labs/god/worker --extra dev
uv run --project labs/god/worker python -m god_worker --check
uv run --project labs/god/worker pytest

cd labs/god/macos && swift run God
```

In the HUD: **Start Session** opens a Realtime conversation (mic streams automatically). Just talk — God replies by voice and can drive the Mac mid-conversation. Type optional text via **Send Text**.

If you move the mouse yourself, God pauses and says to tell it when to continue. Session memory lives in the Realtime conversation for the open session.

Token / $ meter uses `GOD_INPUT_USD_PER_MTK` / `GOD_OUTPUT_USD_PER_MTK`. Model/voice: `GOD_REALTIME_MODEL` (default `gpt-realtime-2.1`), `GOD_REALTIME_VOICE` (default `marin`).

**Stop** / ⌘⇧. tears down Realtime and releases input. Grant Accessibility, Screen Recording, and Microphone.

Docs: [SPEC.md](SPEC.md) · [SPEC-realtime.md](SPEC-realtime.md) · [CAPABILITY-MAP.md](CAPABILITY-MAP.md)
