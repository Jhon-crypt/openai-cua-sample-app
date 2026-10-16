# Spec: voice-session

## Objective

While a God session is open, capture mic audio, transcribe via OpenAI Audio Transcriptions, and treat the text as a goal (and/or policy update).

## Approach

HUD records short utterances (hold or tap-to-talk that stays armed for the session). Audio file → OpenAI `audio.transcriptions` → append to transcript → optional auto-run agent.

## Success Criteria

- Mic button records → transcript appears in log
- User can Run the transcript as a goal
- Kill switch still works during agent runs
