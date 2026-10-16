"""Desktop helpers shared by the God worker (Retina-safe screenshots, app launch)."""

from __future__ import annotations

import subprocess
from typing import Any


def normalize_screenshots(pyautogui: Any) -> None:
    """Make screenshot pixels match PyAutoGUI logical coordinates (Retina-safe)."""
    from PIL import Image

    capture = pyautogui.screenshot

    def screenshot(imageFilename=None, region=None, **kwargs):
        image = capture(**kwargs)
        size = tuple(pyautogui.size())
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        if region is not None:
            x, y, width, height = region
            image = image.crop((x, y, x + width, y + height))
        if imageFilename is not None:
            image.save(imageFilename)
        return image

    pyautogui.screenshot = screenshot


def open_app(name: str) -> str:
    """Launch or focus a macOS app by name (more reliable than Spotlight typing)."""
    completed = subprocess.run(
        ["open", "-a", name],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "open failed").strip()
        raise RuntimeError(f"Could not open app {name!r}: {err}")
    return f"opened {name}"
