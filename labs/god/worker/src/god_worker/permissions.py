"""macOS desktop permission checks."""

from __future__ import annotations

import ctypes
import sys


def check_desktop(pyautogui) -> tuple[int, int]:
    width, height = pyautogui.size()
    if width <= 0 or height <= 0:
        raise RuntimeError("No local desktop is available. Use a graphical session.")
    if sys.platform == "darwin":
        services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        if not services.AXIsProcessTrusted():
            raise RuntimeError(
                "macOS Accessibility permission is required. Enable the app launching "
                "God (Terminal or the God app) in System Settings > Privacy & Security > Accessibility."
            )
        import Quartz

        if not Quartz.CGPreflightScreenCaptureAccess():
            raise RuntimeError(
                "macOS Screen Recording permission is required. Enable it in System Settings > "
                "Privacy & Security > Screen Recording, then restart the launcher."
            )
    return width, height
