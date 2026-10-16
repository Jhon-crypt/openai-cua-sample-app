# Spec: agent-loop

## Objective

Run a Responses API → `exec_py` → tool results loop inside the God worker, driven by a goal (+ optional spoken policy) from the HUD.

## Approach

Mirror `python-app/app/responses_loop.py` (not OpenAI Agents SDK `ComputerTool`) so PyAutoGUI code execution stays consistent with this repo.

## Protocol

```json
{"type":"agent_run","id":"1","goal":"...","policy":"...","max_turns":12}
{"type":"agent_stop","id":"2"}
```

Events (stdout, no blocking response id required for stream):

```json
{"type":"agent_event","level":"info","message":"..."}
{"type":"result","id":"1","ok":true,"detail":"final assistant text"}
```

## Success Criteria

- Typed goal in HUD runs the loop and streams narration
- Stop cancels the in-flight run and releases input
- Missing API key returns a clear error
