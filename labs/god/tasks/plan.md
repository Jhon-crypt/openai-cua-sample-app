# Plan: God

1. **Slice 1 — Bridge** (`shell-hud` + `desktop-worker`): stdio JSON-lines, Test Click, Stop.
2. **Slice 2 — Agent loop:** wire OpenAI + screenshot observe (reuse patterns from `python-app`).
3. **Slice 3 — Voice session:** hotkey opens mic; STT streams into goals/policy while acting.
4. **Slice 4 — Session log:** persist transcript + actions under `labs/god/data/` (gitignored).

**Risks:** macOS permissions; Retina coordinates; held keys after crash → mitigate with `--check`, stop/release, failsafe corner.
