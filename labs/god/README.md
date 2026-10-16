# God

Personal **voice Mac operator** workspace. Not a browser lab template — do **not** register in `catalog.json`.

## Slice 1 (now)

Swift HUD + Python worker bridge: Start Session, Test Click, Stop (⌘⇧.).

```bash
# Permissions check
uv sync --project labs/god/worker --extra dev
uv run --project labs/god/worker python -m god_worker --check

# Worker tests
uv run --project labs/god/worker pytest

# HUD
cd labs/god/macos && swift build && swift run God
```

Grant **Accessibility** and **Screen Recording** to Terminal (or the God binary) before Test Click.

Docs: [SPEC.md](SPEC.md) · [CAPABILITY-MAP.md](CAPABILITY-MAP.md) · [ideas/voice-mac-operator.md](ideas/voice-mac-operator.md)
