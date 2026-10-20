"""Background desktop agent — keeps Realtime voice free while tools run."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from god_worker.agent_loop import run_agent_loop

Emit = Callable[[dict[str, Any]], None]
OnComplete = Callable[[dict[str, Any]], None]


class DesktopTaskRunner:
    """One-at-a-time Responses agent_loop in a daemon thread."""

    def __init__(
        self,
        *,
        pyautogui: Any,
        emit: Emit,
        policy: str,
        meter: Any | None = None,
        on_complete: OnComplete | None = None,
    ) -> None:
        self.pyautogui = pyautogui
        self.emit = emit
        self.policy = policy
        self.meter = meter
        self.on_complete = on_complete
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._status = "idle"
        self._goal = ""
        self._task_id: str | None = None
        self._last_result = ""
        self._started_at = 0.0
        self._finished_at = 0.0

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    def is_running(self) -> bool:
        return self.status == "running"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "status": self._status,
                "task_id": self._task_id,
                "goal": self._goal,
                "last_result": self._last_result,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
            }

    def start(self, goal: str, task_id: str | None = None) -> dict[str, Any]:
        goal = (goal or "").strip()
        if not goal:
            return {"ok": False, "message": "goal is required"}
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {
                    "ok": False,
                    "started": False,
                    "status": "running",
                    "message": "Desktop task already running — cancel first or wait.",
                    "goal": self._goal,
                }
            self._cancel = threading.Event()
            self._status = "running"
            self._goal = goal
            self._task_id = task_id
            self._last_result = ""
            self._started_at = time.time()
            self._finished_at = 0.0
            self._thread = threading.Thread(
                target=self._run,
                args=(goal, task_id, self._cancel),
                name="god-desktop-task",
                daemon=True,
            )
            self._thread.start()
        self.emit(
            {
                "type": "agent_event",
                "level": "pending",
                "message": f"Desktop task started: {goal[:160]}{'…' if len(goal) > 160 else ''}",
            }
        )
        return {"ok": True, "started": True, "status": "running", "goal": goal, "task_id": task_id}

    def replace(self, goal: str, task_id: str | None = None) -> dict[str, Any]:
        """Cancel any running task, then start `goal` once it exits. Never blocks the caller."""
        with self._lock:
            previous = self._thread
        if previous is None or not previous.is_alive():
            return self.start(goal, task_id=task_id)
        self.cancel()

        def _start_after_exit() -> None:
            previous.join(timeout=120.0)
            result = self.start(goal, task_id=task_id)
            if not result.get("ok"):
                self.emit({"type": "agent_event", "level": "error", "message": result.get("message", "replace failed")})

        threading.Thread(target=_start_after_exit, name="god-desktop-replace", daemon=True).start()
        return {"ok": True, "started": True, "queued": True, "goal": goal, "task_id": task_id}

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            was_running = self._status == "running"
            self._cancel.set()
            if was_running:
                self._status = "cancelling"
        if was_running:
            self.emit(
                {
                    "type": "agent_event",
                    "level": "info",
                    "message": "Desktop task cancel requested.",
                }
            )
            return {"ok": True, "cancelling": True, "status": "cancelling"}
        return {"ok": True, "cancelling": False, "status": self.status}

    def stop_join(self, timeout: float = 2.0) -> None:
        self.cancel()
        thread = None
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self, goal: str, task_id: str | None, cancel: threading.Event) -> None:
        payload: dict[str, Any]
        try:
            text = run_agent_loop(
                goal=goal,
                policy=self.policy,
                pyautogui=self.pyautogui,
                emit=self.emit,
                cancel=cancel,
                max_turns=20,
                meter=self.meter,
                talk=False,
            )
            with self._lock:
                self._status = "done"
                self._last_result = text
                self._finished_at = time.time()
            payload = {
                "ok": True,
                "status": "done",
                "task_id": task_id,
                "goal": goal,
                "result": text,
            }
            self.emit(
                {
                    "type": "agent_event",
                    "level": "ok",
                    "message": f"Desktop task done: {text[:200]}{'…' if len(text) > 200 else ''}",
                }
            )
        except Exception as error:  # noqa: BLE001 - surface to voice layer
            cancelled = cancel.is_set()
            status = "cancelled" if cancelled else "error"
            message = "Desktop task cancelled." if cancelled else str(error)
            with self._lock:
                self._status = status
                self._last_result = message
                self._finished_at = time.time()
            payload = {
                "ok": False,
                "status": status,
                "task_id": task_id,
                "goal": goal,
                "result": message,
            }
            self.emit(
                {
                    "type": "agent_event",
                    "level": "info" if cancelled else "error",
                    "message": message,
                }
            )
        finally:
            with self._lock:
                self._thread = None
        if self.on_complete is not None:
            try:
                self.on_complete(payload)
            except Exception as error:  # noqa: BLE001
                self.emit(
                    {
                        "type": "agent_event",
                        "level": "error",
                        "message": f"desktop on_complete failed: {error}",
                    }
                )
