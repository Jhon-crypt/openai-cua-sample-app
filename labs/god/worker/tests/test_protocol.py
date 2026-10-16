from god_worker.protocol import handle_request, parse_line


def test_parse_line_requires_type():
    assert parse_line('{"type":"ping","id":"1"}')["type"] == "ping"


def test_unknown_type_returns_error():
    result = handle_request({"type": "nope", "id": "9"}, {})
    assert result["ok"] is False
    assert result["id"] == "9"


def test_handler_success():
    result = handle_request(
        {"type": "ping", "id": "1"},
        {"ping": lambda _req: {"type": "pong", "detail": "ok"}},
    )
    assert result == {"type": "pong", "detail": "ok", "id": "1", "ok": True}


def test_handler_exception_becomes_error():
    def boom(_req):
        raise RuntimeError("nope")

    result = handle_request({"type": "ping", "id": "2"}, {"ping": boom})
    assert result["ok"] is False
    assert "nope" in result["error"]
