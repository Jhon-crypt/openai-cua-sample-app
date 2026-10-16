"""Stuck detection for repeated identical failing tool calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StuckDetector:
    remind_after: int = 5
    stop_after: int = 8
    _key: str | None = None
    _count: int = 0
    _reminded: bool = False
    history: list[str] = field(default_factory=list)

    def observe(self, tool: str, arguments_key: str, *, ok: bool) -> dict[str, Any]:
        key = f"{tool}|{arguments_key}|{'ok' if ok else 'fail'}"
        if ok:
            self._key = None
            self._count = 0
            self._reminded = False
            return {"stuck": False}

        if key == self._key:
            self._count += 1
        else:
            self._key = key
            self._count = 1
            self._reminded = False

        self.history.append(key)
        if len(self.history) > 40:
            self.history = self.history[-40:]

        if self._count >= self.stop_after:
            return {
                "stuck": True,
                "action": "stop",
                "count": self._count,
                "message": (
                    f"Stuck: {tool} failed {self._count} times with the same args. "
                    "Stopping desktop actions — tell the operator and wait for guidance."
                ),
            }
        if self._count >= self.remind_after and not self._reminded:
            self._reminded = True
            return {
                "stuck": True,
                "action": "remind",
                "count": self._count,
                "message": (
                    f"You repeated a failing {tool} {self._count} times. "
                    "Change approach: screenshot, pick different coords, or try another app path."
                ),
            }
        return {"stuck": False, "count": self._count}
