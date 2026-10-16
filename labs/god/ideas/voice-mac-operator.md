# Voice Mac Operator (God)

## Problem Statement

How might I continuously voice-steer my own Mac through everyday GUI work, see what the agent is doing, and interrupt it instantly — with do’s and don’ts set by what I say, not a fixed app deny-list?

## Recommended Direction

**Native overlay co-pilot** (SwiftUI HUD) with a Python worker behind it (reuse this repo’s observe → act → observe loop / PyAutoGUI style executor).

North star: open-ended “use my Mac like I do.” MVP behaves like a session-based co-pilot with an emergency kill switch, not unbounded day-one autonomy.

## Decisions (locked)

| Topic | Choice |
|-------|--------|
| Audience | Only me |
| Shell | Native Mac app (mandatory) |
| Executor | Python worker behind Swift UI (OK) |
| Workspace | `labs/god/` (this folder) |
| Mic | Wake word or hotkey starts a session; **mic stays open** while the agent acts until I stop the session |
| Policy | Do’s and don’ts controlled by what I say (spoken, per session) |
| Safety net | Mechanical kill switch (hotkey / failsafe) — always works even if the model ignores speech |

## Key Assumptions to Validate

- [ ] End-to-end “say open Notes → Notes frontmost” in < ~5s feels acceptable
- [ ] Barge-in / hotkey “stop” releases keys/mouse within ~500ms
- [ ] Accessibility + Screen Recording stay usable across restarts
- [ ] Spoken policy is enough day-to-day when paired with a kill switch
- [ ] Continuous STT cost/latency is tolerable for solo sessions

## MVP Scope

**In:** SwiftUI HUD; session mic (wake/hotkey → stay open); live transcript; agent narration; screenshots; click/type/scroll/hotkeys; kill switch; session log; single primary display.

**Out:** App Store; multi-user; hardcoded forbid-app product; confirm-every-click as default UX; multi-monitor perfection; password autofill; purchases automation.

**First slices:** (1) native window + kill switch + one scripted click (2) voice → Notes (3) continuous listen while acting.

## Not Doing (and Why)

- Wiring God into `labs/catalog.json` as a sample scenario — wrong product shape for a native Mac operator
- Open-ended no-guardrails with no kill switch — spoken policy alone is not a guarantee
- Rewriting the entire agent in Swift before a working loop — slows learning
- Confirm-every-action as the main mode — conflicts with continuous co-pilot feel

## Open Questions

- Wake phrase wording (e.g. “Hey God”) vs hotkey-only to open the ear
- Whether spoken rules can be pinned with “remember this” across sessions
- Exact hotkey for stop / session end
