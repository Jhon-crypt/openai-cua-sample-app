# God

Personal **voice Mac operator** workspace. Not a browser lab template — do **not** register in `catalog.json`.

## Run

Requires root `.env` with `OPENAI_API_KEY`. Voice = **GPT-Live** (`gpt-live-1`, client delegation): it only answers when addressed and hands every Mac action to a **background desktop runner** (Responses + `exec_py`, short batches, stuck detection). Set `GOD_VOICE_BACKEND=realtime` to fall back to the Realtime API. See [SPEC-live.md](SPEC-live.md).

```bash
uv sync --project labs/god/worker --extra dev   # pulls openai[realtime] (websockets)
uv run --project labs/god/worker python -m god_worker --check
uv run --project labs/god/worker pytest

cd labs/god/macos && swift run God
```

Build the HUD with Swift ≥ 6.3.3. Swift 6.3.2 (Xcode 26.5) miscompiles main-actor checks and the HUD crashes mid-session ([swiftlang/swift#89197](https://github.com/swiftlang/swift/issues/89197)). With the swift.org toolchain installed in `~/Library/Developer/Toolchains`:

```bash
export TOOLCHAINS=$(plutil -extract CFBundleIdentifier raw ~/Library/Developer/Toolchains/swift-6.3.3-RELEASE.xctoolchain/Info.plist)
swift --version   # Swift 6.3.3
```

In the HUD: **Start Session** opens a Live conversation (mic streams automatically). Talk to God when you want something; it stays silent for room noise and chatter. Mac goals run in the background (red cursor ring) while you keep talking; say “stop” to cancel, or correct it mid-task. **Send Text** sends a goal straight to the desktop runner.

If you move the mouse yourself **while a desktop task is running**, God stops that task and tells you.

Cost meter: Live voice at `GOD_LIVE_USD_PER_MIN` (default $0.05/min, billed while the session is open — press Stop when idle) plus desktop-agent tokens at `GOD_INPUT_USD_PER_MTK` / `GOD_OUTPUT_USD_PER_MTK`. Live model/voice: `GOD_LIVE_MODEL` (default `gpt-live-1`), `GOD_LIVE_VOICE` (default `marin`). Realtime fallback: `GOD_REALTIME_MODEL` (default `gpt-realtime-2.1`), `GOD_REALTIME_VOICE`.

**Stop** / ⌘⇧. closes the voice session and releases input. Grant Accessibility, Screen Recording, and Microphone.

Docs: [AGENTS.md](AGENTS.md) (start here) · [SPEC.md](SPEC.md) · [SPEC-live.md](SPEC-live.md) · [SPEC-realtime.md](SPEC-realtime.md) · [CAPABILITY-MAP.md](CAPABILITY-MAP.md)
