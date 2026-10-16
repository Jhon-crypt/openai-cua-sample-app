"""Tests for screenshot↔screen mapping and tool wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from PIL import Image

from god_worker.grounding import map_image_to_screen
from god_worker.gui_tools import DesktopHarness, build_gui_tool_definitions


def test_map_image_to_screen_scales_1024_to_full():
    # Model sees 1024x661; real logical screen 1512x982 (same aspect).
    x, y = map_image_to_screen(512, 330, img_w=1024, img_h=661, screen_w=1512, screen_h=982)
    assert x == 756
    assert abs(y - 490) <= 1


def test_click_maps_screenshot_coords(monkeypatch):
    pag = MagicMock()
    pag.size.return_value = (2000, 1000)
    # Logical screenshot already screen-sized; downscale path uses max_width 1024 → 1024x512
    pag.screenshot.return_value = Image.new("RGB", (2000, 1000), color=(10, 20, 30))
    clicks: list[tuple] = []

    def fake_click(x, y, button="left"):
        clicks.append((x, y, button))

    pag.click.side_effect = fake_click

    harness = DesktopHarness(pag)
    # Prime last screenshot meta via screenshot tool
    shot = harness.dispatch("screenshot", {})
    assert shot["ok"] is True
    assert shot["img_w"] == 1024
    assert shot["screen_w"] == 2000

    # Click center of downscaled image → should map near screen center
    out = harness.dispatch("click", {"x": 512, "y": 256})
    assert out["ok"] is True
    assert out["screen_x"] == 1000
    assert out["screen_y"] == 500
    assert clicks[-1][0] == 1000
    assert clicks[-1][1] == 500


def test_tool_defs_include_grounding_tools():
    names = {t["name"] for t in build_gui_tool_definitions()}
    assert {"click_text", "click_element", "click", "screenshot"} <= names
