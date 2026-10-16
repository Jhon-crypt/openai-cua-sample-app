# Spec: desktop-worker

## Objective

Long-lived Python process controlled by JSON-lines on stdin; drives primary display input; releases held input on `stop`.

## Protocol (JSON lines)

**Requests (stdin):**

```json
{"type":"ping","id":"1"}
{"type":"click_center","id":"2"}
{"type":"stop","id":"3"}
```

**Responses (stdout):**

```json
{"type":"pong","id":"1","ok":true}
{"type":"result","id":"2","ok":true,"detail":"clicked"}
{"type":"result","id":"3","ok":true,"detail":"released"}
{"type":"error","id":"2","ok":false,"error":"..."}
```

## Success Criteria

- `--check` validates Accessibility / Screen Recording (same spirit as python-app)
- `ping` / `click_center` / `stop` work
- `stop` attempts to release mouse buttons and common modifier keys

## Boundaries

- Always: failsafe-friendly; log errors as JSON not stack traces to stdout
- Never: call the model from the worker in slice 1
