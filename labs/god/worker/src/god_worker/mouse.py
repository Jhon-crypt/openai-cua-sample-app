"""Make mouse motion visible and report coordinates to the HUD."""

from __future__ import annotations

from typing import Any, Callable

Emit = Callable[[dict[str, Any]], None]


def install_visible_mouse(pyautogui: Any, emit: Emit, duration: float = 0.45) -> None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.08
    original_move = pyautogui.moveTo
    original_click = pyautogui.click
    original_drag = getattr(pyautogui, "dragTo", None)

    def _emit_pos(x: Any, y: Any) -> None:
        try:
            emit({"type": "mouse", "x": int(x), "y": int(y)})
        except Exception:  # noqa: BLE001
            pass

    def moveTo(*args, **kwargs):
        if len(args) >= 2:
            x, y = args[0], args[1]
            args = args[2:]
        else:
            x = kwargs.pop("x", None)
            y = kwargs.pop("y", None)
        if x is None or y is None:
            return original_move(*args, **kwargs)
        kwargs.setdefault("duration", duration)
        _emit_pos(x, y)
        return original_move(x, y, *args, **kwargs)

    def click(*args, **kwargs):
        x = kwargs.pop("x", None)
        y = kwargs.pop("y", None)
        move_duration = kwargs.pop("duration", duration)
        if len(args) >= 2:
            x, y = args[0], args[1]
            args = args[2:]
        if x is not None and y is not None:
            moveTo(x, y, duration=move_duration)
            return original_click(*args, **kwargs)
        cur = pyautogui.position()
        _emit_pos(cur.x, cur.y)
        return original_click(*args, **kwargs)

    pyautogui.moveTo = moveTo
    pyautogui.click = click

    if original_drag is not None:

        def dragTo(*args, **kwargs):
            kwargs.setdefault("duration", max(duration, 0.5))
            if len(args) >= 2:
                _emit_pos(args[0], args[1])
            elif "x" in kwargs and "y" in kwargs:
                _emit_pos(kwargs["x"], kwargs["y"])
            return original_drag(*args, **kwargs)

        pyautogui.dragTo = dragTo
