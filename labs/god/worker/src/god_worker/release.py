"""Release held mouse/keyboard input."""

from __future__ import annotations

import sys
import time


def release_inputs() -> None:
    if sys.platform == "darwin":
        _release_macos()
        return
    try:
        import pyautogui

        for button in ("left", "middle", "right"):
            try:
                pyautogui.mouseUp(button=button)
            except Exception:  # noqa: BLE001
                pass
        for key in ("shift", "ctrl", "alt", "command", "option"):
            try:
                pyautogui.keyUp(key)
            except Exception:  # noqa: BLE001
                pass
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Failed to release inputs: {error}") from error


def _release_macos() -> None:
    import ctypes

    import Quartz

    services = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    if not services.AXIsProcessTrusted():
        raise RuntimeError("Accessibility permission is required to release desktop input.")

    position = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))

    def post(event, clear_flags: bool = False) -> None:
        if event is None:
            raise RuntimeError("Could not create an input release event.")
        if clear_flags:
            Quartz.CGEventSetFlags(event, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    for key in range(128):
        post(Quartz.CGEventCreateKeyboardEvent(None, key, False), True)

    for event_type, native_button in (
        (Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft),
        (Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
        (Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
    ):
        time.sleep(0.01)
        post(Quartz.CGEventCreateMouseEvent(None, event_type, position, native_button))
