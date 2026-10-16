"""JSON-lines request handling."""

from __future__ import annotations

import json
from typing import Any, Callable


Handler = Callable[[dict[str, Any]], dict[str, Any]]


def parse_line(line: str) -> dict[str, Any]:
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError("Request must be a JSON object.")
    if "type" not in payload:
        raise ValueError("Request requires a type field.")
    return payload


def handle_request(request: dict[str, Any], handlers: dict[str, Handler]) -> dict[str, Any]:
    request_id = request.get("id")
    request_type = request["type"]
    handler = handlers.get(request_type)
    if handler is None:
        return {"type": "error", "id": request_id, "ok": False, "error": f"Unknown type: {request_type}"}
    try:
        result = handler(request)
        result.setdefault("id", request_id)
        result.setdefault("ok", True)
        return result
    except Exception as error:  # noqa: BLE001 - surface to the HUD as JSON
        return {"type": "error", "id": request_id, "ok": False, "error": str(error)}
