"""Make mouse motion visible and report coordinates to the HUD."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

Emit = Callable[[dict[str, Any]], None]

# Settle after the last agent mouse call so OS cursor lag doesn't look like the operator.
_DEFAULT_SETTLE_S = 0.35


class AgentMouseGuard:
    """Tracks when *we* are driving the cursor so the pause-watcher can ignore it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth = 0
        self._settle_until = 0.0

    def begin(self) -> None:
        with self._lock:
            self._depth += 1

    def end(self, settle_s: float = _DEFAULT_SETTLE_S) -> None:
        with self._lock:
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._settle_until = time.time() + max(0.0, settle_s)

    def is_active(self) -> bool:
        with self._lock:
            if self._depth > 0:
                return True
            return time.time() < self._settle_until


_GUARD = AgentMouseGuard()


def agent_mouse_guard() -> AgentMouseGuard:
    return _GUARD


def is_agent_controlling_mouse() -> bool:
    return _GUARD.is_active()


@contextmanager
def agent_mouse_section(settle_s: float = _DEFAULT_SETTLE_S) -> Iterator[None]:
    """Mark a block of code as agent-owned mouse/keyboard pointing motion."""
    _GUARD.begin()
    try:
        yield
    finally:
        _GUARD.end(settle_s=settle_s)


def install_visible_mouse(pyautogui: Any, emit: Emit, duration: float = 0.65) -> None:
    """Slow moves + HUD ring updates so the operator can watch the agent cursor."""
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    original_move = pyautogui.moveTo
    original_click = pyautogui.click
    original_drag = getattr(pyautogui, "dragTo", None)

    def _emit_pos(x: Any, y: Any) -> None:
        try:
            emit({"type": "mouse", "x": int(x), "y": int(y)})
        except Exception:  # noqa: BLE001
            pass

    def moveTo(*args, **kwargs):
        with agent_mouse_section():
            if len(args) >= 2:
                x, y = args[0], args[1]
                args = args[2:]
            else:
                x = kwargs.pop("x", None)
                y = kwargs.pop("y", None)
            if x is None or y is None:
                return original_move(*args, **kwargs)
            move_duration = float(kwargs.setdefault("duration", duration) or duration)
            try:
                cur = pyautogui.position()
                _emit_pos(cur.x, cur.y)
            except Exception:  # noqa: BLE001
                pass
            _emit_pos(x, y)
            # Emit a few midpoints so the red ring tracks the path, not just the endpoint.
            try:
                steps = max(2, min(6, int(move_duration / 0.12)))
                x0, y0 = float(pyautogui.position().x), float(pyautogui.position().y)
                tx, ty = float(x), float(y)
                for i in range(1, steps):
                    t = i / steps
                    _emit_pos(x0 + (tx - x0) * t, y0 + (ty - y0) * t)
            except Exception:  # noqa: BLE001
                pass
            result = original_move(x, y, *args, **kwargs)
            _emit_pos(x, y)
            return result

    def click(*args, **kwargs):
        with agent_mouse_section():
            x = kwargs.pop("x", None)
            y = kwargs.pop("y", None)
            move_duration = kwargs.pop("duration", duration)
            if len(args) >= 2:
                x, y = args[0], args[1]
                args = args[2:]
            if x is not None and y is not None:
                # Nested moveTo also arms the guard (depth>1) — fine.
                moveTo(x, y, duration=move_duration)
                time.sleep(0.05)
                return original_click(*args, **kwargs)
            cur = pyautogui.position()
            _emit_pos(cur.x, cur.y)
            return original_click(*args, **kwargs)

    def dragTo(*args, **kwargs):
        with agent_mouse_section():
            kwargs.setdefault("duration", max(duration, 0.55))
            if len(args) >= 2:
                _emit_pos(args[0], args[1])
            elif "x" in kwargs and "y" in kwargs:
                _emit_pos(kwargs["x"], kwargs["y"])
            return original_drag(*args, **kwargs)

    pyautogui.moveTo = moveTo
    pyautogui.click = click

    if original_drag is not None:
        pyautogui.dragTo = dragTo
