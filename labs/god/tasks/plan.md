# Plan: God

Done:

1. **Bridge** (`shell-hud` + `desktop-worker`): stdio JSON-lines, Test Click, Stop.
2. **Agent loop:** Responses + `exec_py` desktop agent, plan / stuck / context harness.
3. **Voice:** Whisper push-to-talk → Realtime duplex → **GPT-Live** with client delegation (current, [SPEC-live.md](../SPEC-live.md)).
4. **Voice/tools split:** background `DesktopTaskRunner` so voice never blocks on GUI work.

Next (details in [todo.md](todo.md)):

5. **Live manual checks** against the success criteria in SPEC-live.
6. **Speaker barge-in:** Apple voice-processing echo cancellation so the mic can stay open on laptop speakers.
7. **Push-to-talk / idle auto-close** via `session.input_audio.mute` (ask first).
8. **Session log:** persist transcript + actions under `labs/god/data/` (gitignored).

**Risks:** macOS permissions; Retina coordinates; held keys after crash (mitigated by `--check`, stop/release, failsafe corner); Swift toolchain regressions (build with ≥ 6.3.3).
