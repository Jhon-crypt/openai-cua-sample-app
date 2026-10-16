"""Unit tests for Realtime session helpers (no network)."""

from god_worker.realtime_session import build_instructions, build_realtime_tools, session_config


def test_instructions_mention_mouse_interrupt_and_gui_tools():
    text = build_instructions("Do not delete files.", plan="1. open Notes")
    assert "moved the mouse" in text
    assert "Do not delete files." in text
    assert "ANY macOS application" in text
    assert "open_app" in text
    assert "update_plan" in text
    assert "click_text" in text
    assert "screenshot pixel" in text.lower() or "img_w" in text
    assert "1. open Notes" in text


def test_realtime_tools_include_gui_and_exec_py():
    tools = build_realtime_tools()
    names = {t["name"] for t in tools}
    assert "exec_py" in names
    assert "click" in names
    assert "click_text" in names
    assert "click_element" in names
    assert "screenshot" in names
    assert all("strict" not in t for t in tools)


def test_session_config_shape():
    cfg = session_config("be careful", "gpt-realtime-2.1", "marin", plan="todo")
    assert cfg["type"] == "realtime"
    assert cfg["model"] == "gpt-realtime-2.1"
    assert cfg["audio"]["input"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["noise_reduction"]["type"] == "far_field"
    assert cfg["audio"]["input"]["turn_detection"]["eagerness"] == "low"
    assert cfg["audio"]["output"]["voice"] == "marin"
    assert "todo" in cfg["instructions"]
    assert {t["name"] for t in cfg["tools"]} >= {"open_app", "exec_py"}
