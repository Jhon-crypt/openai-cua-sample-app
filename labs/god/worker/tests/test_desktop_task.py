"""Unit tests for DesktopTaskRunner (no network)."""

import threading
import time
from unittest.mock import patch

from god_worker.desktop_task import DesktopTaskRunner


def test_start_rejects_empty_goal():
    events: list[dict] = []
    runner = DesktopTaskRunner(pyautogui=object(), emit=events.append, policy="")
    result = runner.start("  ")
    assert result["ok"] is False
    assert runner.status == "idle"


def test_start_returns_immediately_and_runs_in_background():
    events: list[dict] = []
    completed: list[dict] = []
    started = threading.Event()
    release = threading.Event()

    def fake_loop(**kwargs):
        started.set()
        release.wait(timeout=2.0)
        if kwargs["cancel"].is_set():
            raise RuntimeError("Agent stopped by operator.")
        return "opened Notes"

    runner = DesktopTaskRunner(
        pyautogui=object(),
        emit=events.append,
        policy="be careful",
        on_complete=completed.append,
    )
    with patch("god_worker.desktop_task.run_agent_loop", side_effect=fake_loop):
        result = runner.start("Open Notes")
        assert result["ok"] is True
        assert result["started"] is True
        assert runner.is_running()
        assert started.wait(timeout=1.0)
        # Second start while busy is rejected without blocking.
        busy = runner.start("Something else")
        assert busy["ok"] is False
        assert busy["started"] is False
        release.set()
        deadline = time.time() + 2.0
        while runner.is_running() and time.time() < deadline:
            time.sleep(0.02)
        assert not runner.is_running()
        assert runner.status == "done"
        assert completed and completed[0]["ok"] is True
        assert "Notes" in completed[0]["result"]


def test_cancel_stops_running_task():
    events: list[dict] = []
    completed: list[dict] = []
    entered = threading.Event()

    def fake_loop(**kwargs):
        entered.set()
        for _ in range(50):
            if kwargs["cancel"].is_set():
                raise RuntimeError("Agent stopped by operator.")
            time.sleep(0.02)
        return "should not finish"

    runner = DesktopTaskRunner(
        pyautogui=object(),
        emit=events.append,
        policy="",
        on_complete=completed.append,
    )
    with patch("god_worker.desktop_task.run_agent_loop", side_effect=fake_loop):
        runner.start("Long task")
        assert entered.wait(timeout=1.0)
        cancel_result = runner.cancel()
        assert cancel_result["cancelling"] is True
        deadline = time.time() + 2.0
        while runner.status == "cancelling" and time.time() < deadline:
            time.sleep(0.02)
        assert runner.status == "cancelled"
        assert completed and completed[0]["ok"] is False
