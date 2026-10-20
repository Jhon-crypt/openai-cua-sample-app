"""GPT-Live session wiring (no network)."""

import base64
import json
import threading
import time

from god_worker.live_session import (
    LiveSession,
    TranscriptLog,
    build_live_instructions,
    build_session_start,
    is_cancel_request,
)


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def close(self) -> None:
        pass

    def of_type(self, etype: str) -> list[dict]:
        return [e for e in self.sent if e["type"] == etype]


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.cancelled = 0
        self.running = False
        self.goal = ""

    def replace(self, goal, task_id=None):
        self.started.append((task_id, goal))
        self.running = True
        self.goal = goal
        return {"ok": True, "started": True, "task_id": task_id}

    def cancel(self):
        self.cancelled += 1
        return {"ok": True}

    def is_running(self):
        return self.running

    def snapshot(self):
        return {"status": "running" if self.running else "idle", "goal": self.goal, "finished_at": 0}

    def stop_join(self, timeout=0):
        pass


def make_session():
    events: list[dict] = []
    session = LiveSession(policy="Be careful.", pyautogui=object(), emit=events.append, meter=None, cancel=threading.Event())
    ws = FakeWS()
    session._ws = ws
    runner = FakeRunner()
    session._desktop = runner
    return session, ws, runner, events


def user_says(session, text, start_ms):
    session.handle_event({"type": "session.input_transcript.delta", "delta": text, "start_ms": start_ms, "end_ms": start_ms + 300})


def test_session_start_uses_client_delegation_and_pcm24k():
    start = build_session_start("No deletes.", model="gpt-live-1", voice="marin")
    assert start["type"] == "session.start"
    s = start["session"]
    assert s["model"] == "gpt-live-1"
    assert s["delegation"] == {"type": "client"}
    assert s["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert s["audio"]["output"]["voice"] == "marin"
    assert "No deletes." in s["instructions"]


def test_instructions_follow_live_template_and_noise_rules():
    text = build_live_instructions("")
    for heading in ("Backchannel policy:", "Interruption policy:", "Delegation policy:", "Backend tools:"):
        assert heading in text
    assert "Generate <|SpawnThinking|> when:" in text
    assert "Do not generate <|SpawnThinking|> when:" in text
    assert "addresses you directly" in text
    assert "nearby conversation" in text
    assert "open Chrome" in text


def test_is_cancel_request():
    assert is_cancel_request("stop")
    assert is_cancel_request("cancel that please")
    assert is_cancel_request("never mind")
    assert not is_cancel_request("open Chrome")
    assert not is_cancel_request("can you stop by the shop page and then open my orders in safari for me")


def test_transcript_goal_window_drops_old_chatter():
    log = TranscriptLog()
    log.add("You", "tv people talking ", 1_000)
    log.add("You", "open Chrome", 30_000)
    assert log.take_user_goal(offset_ms=31_000) == "open Chrome"
    # Consumed — the next delegation does not reuse it.
    log.add("You", "now Safari", 40_000)
    assert log.take_user_goal(offset_ms=41_000) == "now Safari"


def test_no_audio_before_session_started():
    session, ws, _, _ = make_session()
    session.append_audio("AAAA")
    assert ws.of_type("session.input_audio.append") == []
    session.handle_event({"type": "session.started", "session": {"id": "live_1"}})
    session.append_audio("AAAA")
    assert len(ws.of_type("session.input_audio.append")) == 1
    session._stop.set()


def test_short_command_starts_desktop_task():
    session, ws, runner, _ = make_session()
    session._known_delegations.add("d1")
    user_says(session, "Open Chrome", 5_000)
    session._dispatch_delegation("d1", 5_500)
    assert runner.started == [("d1", "Open Chrome")]
    thinking = ws.of_type("session.thinking.append")
    assert thinking and thinking[-1]["delegation_id"] == "d1"
    # Starting work is silent — nothing spoken until a verified result.
    assert ws.of_type("session.commentary.append") == []


def test_result_spoken_only_for_active_task():
    session, ws, runner, _ = make_session()
    session._known_delegations.update({"d1", "d2"})
    user_says(session, "open Chrome", 1_000)
    session._dispatch_delegation("d1", 1_500)
    user_says(session, "no, Safari", 3_000)
    session._dispatch_delegation("d2", 3_500)
    assert "previous desktop goal" in runner.started[-1][1]

    session._on_desktop_complete({"task_id": "d1", "status": "cancelled", "result": "x"})
    assert ws.of_type("session.commentary.append") == []

    session._on_desktop_complete({"task_id": "d2", "status": "done", "result": "Safari is open."})
    spoken = ws.of_type("session.commentary.append")
    assert len(spoken) == 1
    assert spoken[0]["delegation_id"] == "d2"
    assert "Safari is open." in spoken[0]["content"]


def test_cancel_request_confirms_only_after_runner_cancels():
    session, ws, runner, _ = make_session()
    session._known_delegations.update({"d1", "d2"})
    user_says(session, "open Notes", 1_000)
    session._dispatch_delegation("d1", 1_500)
    user_says(session, "stop", 4_000)
    session._dispatch_delegation("d2", 4_500)
    assert runner.cancelled == 1
    assert ws.of_type("session.commentary.append") == []
    session._on_desktop_complete({"task_id": "d1", "status": "cancelled", "result": ""})
    spoken = ws.of_type("session.commentary.append")
    assert spoken and spoken[0]["delegation_id"] == "d2"
    assert "Stopped" in spoken[0]["content"]


def test_unknown_delegation_id_sent_as_null():
    session, ws, _, _ = make_session()
    session.send_text("open Finder")
    assert all(e["delegation_id"] is None for e in ws.of_type("session.thinking.append"))


def test_barge_in_flushes_playback_only_when_model_yielded():
    session, _, _, events = make_session()
    chunk = base64.b64encode(b"\x00" * 48 * 2000).decode()  # 2 s of audio
    session._note_output_audio(chunk)
    # Model still generating → treat as backchannel/noise.
    session._maybe_flush_playback(now=session._last_out_audio_at + 0.1)
    assert not [e for e in events if e["type"] == "realtime_interrupt"]
    # Model stopped generating while audio is still buffered → user took the turn.
    session._maybe_flush_playback(now=session._last_out_audio_at + 0.5)
    assert [e for e in events if e["type"] == "realtime_interrupt"]


def test_session_closed_ends_loop():
    session, _, _, _ = make_session()
    assert session.handle_event({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 3}}) is False
    assert session._closed.is_set()


def test_delegation_event_dispatches_after_transcript_wait(monkeypatch):
    import god_worker.live_session as live

    monkeypatch.setattr(live, "DELEGATION_TRANSCRIPT_WAIT_S", 0.01)
    session, _, runner, _ = make_session()
    user_says(session, "open Mail", 2_000)
    session.handle_event({"type": "session.delegation.created", "offset_ms": 2_400, "delegation": {"id": "d9", "type": "delegation", "target": "client"}})
    deadline = time.time() + 1
    while not runner.started and time.time() < deadline:
        time.sleep(0.01)
    assert runner.started == [("d9", "open Mail")]
