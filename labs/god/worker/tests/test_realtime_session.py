"""Unit tests for Realtime session helpers (no network)."""

from god_worker.realtime_session import build_instructions, build_realtime_tools, session_config


def test_instructions_mention_mouse_interrupt():
    text = build_instructions("Do not delete files.")
    assert "moved the mouse" in text
    assert "Do not delete files." in text
    assert "ANY macOS application" in text
    assert "open_app" in text


def test_realtime_tools_include_exec_py_without_strict():
    tools = build_realtime_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "exec_py"
    assert "strict" not in tools[0]


def test_session_config_shape():
    cfg = session_config("be careful", "gpt-realtime-2.1", "marin")
    assert cfg["type"] == "realtime"
    assert cfg["model"] == "gpt-realtime-2.1"
    assert cfg["audio"]["input"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["noise_reduction"]["type"] == "far_field"
    assert cfg["audio"]["input"]["turn_detection"]["eagerness"] == "low"
    assert cfg["audio"]["output"]["voice"] == "marin"
    assert cfg["tools"][0]["name"] == "exec_py"
