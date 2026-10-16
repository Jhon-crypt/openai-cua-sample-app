"""Tests for structured GUI tools, stuck detector, and context manager."""

from __future__ import annotations

from unittest.mock import MagicMock

from PIL import Image

from god_worker.context_manager import ContextManager
from god_worker.gui_tools import DesktopHarness, build_gui_tool_definitions, tool_result_for_model
from god_worker.stuck import StuckDetector


def test_gui_tool_definitions_include_core_and_exec_py():
    tools = build_gui_tool_definitions()
    names = {t["name"] for t in tools}
    assert {
        "open_app",
        "screenshot",
        "click",
        "click_text",
        "click_element",
        "type_text",
        "hotkey",
        "update_plan",
        "exec_py",
    } <= names
    assert all("strict" not in t for t in tools)


def test_tool_result_omits_base64():
    slim = tool_result_for_model(
        {
            "ok": True,
            "width": 10,
            "height": 10,
            "image_base64": "AAAA",
            "stub": "[screenshot]",
        }
    )
    assert "AAAA" not in slim
    assert "has_image" in slim


def test_update_plan_and_click(monkeypatch):
    events: list[dict] = []
    pag = MagicMock()
    pag.screenshot.return_value = Image.new("RGB", (200, 100), color=(1, 2, 3))
    pag.size.return_value = (200, 100)

    harness = DesktopHarness(pag, emit=events.append)
    plan = harness.dispatch("update_plan", {"plan": "1. open Safari\n2. search"})
    assert plan["ok"] is True
    assert "Safari" in harness.plan

    pag.size.return_value = (200, 100)
    out = harness.dispatch("click", {"x": 10, "y": 20})
    assert out["ok"] is True
    pag.click.assert_called()
    assert "screen_x" in out
    assert out["verify"]["width"] == 200 or out["verify"]["width"] <= 1024


def test_stuck_detector_reminds_then_stops():
    d = StuckDetector(remind_after=3, stop_after=5)
    for _ in range(2):
        assert d.observe("click", '{"x":1}', ok=False)["stuck"] is False
    remind = d.observe("click", '{"x":1}', ok=False)
    assert remind["action"] == "remind"
    d.observe("click", '{"x":1}', ok=False)
    stop = d.observe("click", '{"x":1}', ok=False)
    assert stop["action"] == "stop"


def test_context_manager_elides_bulky():
    cm = ContextManager(soft_chars=100, hard_chars=200, keep_recent=2)
    cm.add("screenshot", "x" * 500, bulky=True)
    cm.add("click", "ok", bulky=False)
    cm.add("screenshot", "y" * 500, bulky=True)
    cm.add("type_text", "hi", bulky=True)
    block = cm.prompt_block()
    assert "stub" in block.lower() or "elided" in block.lower() or "Running summary" in block or "Recent" in block
