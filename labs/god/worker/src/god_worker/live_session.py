"""GPT-Live voice session — full-duplex voice, client delegation to the desktop runner."""

from __future__ import annotations

import base64
import itertools
import json
import os
import threading
import time
from typing import Any, Callable

from god_worker.desktop_task import DesktopTaskRunner
from god_worker.mouse import is_agent_controlling_mouse

Emit = Callable[[dict[str, Any]], None]
Connect = Callable[[str, dict[str, str]], Any]

LIVE_URL = "wss://api.openai.com/v1/live/sessions"
DEFAULT_MODEL = "gpt-live-1"
DEFAULT_VOICE = "marin"
PCM_BYTES_PER_MS = 48  # pcm16 mono @ 24 kHz
# Delegation events carry no task text; transcription can trail the event slightly.
DELEGATION_TRANSCRIPT_WAIT_S = 0.8
TRANSCRIPT_FLUSH_IDLE_S = 1.2
# Only user speech this close before the delegation counts as the request (drops earlier room chatter).
GOAL_WINDOW_MS = 12_000
# Model stopped sending audio this long ago while the user talks → it yielded the turn.
YIELD_GAP_S = 0.35
MIN_BUFFERED_MS_TO_FLUSH = 150
APPEND_MAX_CHARS = 1500  # Live caps append content at 500 tokens.
WATCH_POLL_S = 0.15
MOUSE_MOVE_THRESHOLD_PX = 64
MOUSE_SUSTAINED_POLLS = 2

_CANCEL_WORDS = {"stop", "cancel", "abort", "halt", "quit"}
_CANCEL_PHRASES = ("never mind", "nevermind", "forget it", "hold on", "leave it")


def build_live_instructions(policy: str) -> str:
    """Prompt layout follows the Prompting GPT-Live template (keep the three policy headings)."""
    return "\n".join(
        [
            "You are God, a calm, concise voice assistant that operates John's Mac for him.",
            "Speak briefly and naturally. One or two short sentences unless asked for more.",
            "",
            "Respond when the user addresses you directly or asks for something on the Mac.",
            "Otherwise, keep listening and stay silent.",
            "Keep listening while the user pauses to think.",
            "Do not treat a cough, music, TV, typing, or nearby conversation as a new request.",
            "If you are not sure the user is talking to you, stay silent.",
            "",
            "Backchannel policy: Use minimal backchannels. Never fill silence.",
            "",
            "Interruption policy: Stop speaking when the user interrupts. Listen to what they say.",
            "",
            "Delegation policy:",
            "Backend tools:",
            "- Mac desktop: open any app, click, type, browse websites, and read what is on screen.",
            "",
            "Generate <|SpawnThinking|> when:",
            "- The user asks you to do anything on the Mac, however short (e.g. 'open Chrome').",
            "- A correction changes desktop work already requested.",
            "- The user asks you to stop or cancel what you are doing on the Mac.",
            "",
            "Do not generate <|SpawnThinking|> when:",
            "- The user greets you or chats without asking for a Mac action.",
            "- You cannot tell what they want without a brief clarification.",
            "- The speech is not addressed to you.",
            "",
            "Generate <|SpawnThinking|> before giving an answer that depends on desktop work.",
            "Do not guess the result while waiting. Never say a Mac action is done until the backend says so.",
            "",
            f"Operator policy:\n{policy.strip() or '(none — act safely)'}",
        ]
    )


def build_session_start(policy: str, *, model: str, voice: str) -> dict[str, Any]:
    return {
        "type": "session.start",
        "event_id": "god_start",
        "session": {
            "model": model,
            "instructions": build_live_instructions(policy),
            "audio": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "output": {"voice": voice},
            },
            "delegation": {"type": "client"},
        },
    }


def is_cancel_request(text: str) -> bool:
    compact = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in (text or "").lower())
    words = compact.split()
    if not words or len(words) > 8:
        return False
    joined = " ".join(words)
    return any(w in _CANCEL_WORDS for w in words) or any(p in joined for p in _CANCEL_PHRASES)


def clip(text: str, limit: int = APPEND_MAX_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TranscriptLog:
    """Per-speaker fragment buffer with session-timeline offsets (start_ms)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._user: list[tuple[int, str]] = []
        self._consumed_ms = -1
        self._pending: dict[str, list[str]] = {"You": [], "God": []}
        self._last_delta_at: dict[str, float] = {"You": 0.0, "God": 0.0}

    def add(self, speaker: str, delta: str, start_ms: int | None) -> None:
        with self._lock:
            self._pending[speaker].append(delta)
            self._last_delta_at[speaker] = time.time()
            if speaker == "You":
                self._user.append((int(start_ms or 0), delta))
                self._user = self._user[-400:]

    def take_user_goal(self, offset_ms: int | None = None, window_ms: int = GOAL_WINDOW_MS) -> str:
        """User text since the previous delegation, limited to the window before the delegation."""
        with self._lock:
            floor = self._consumed_ms
            if offset_ms is not None:
                floor = max(floor, int(offset_ms) - window_ms)
            fresh = [(ms, t) for ms, t in self._user if ms > floor]
            if not fresh:
                return ""
            self._consumed_ms = max(ms for ms, _ in fresh)
            return "".join(t for _, t in fresh).strip()

    def flush(self, *, force: bool = False, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        lines: list[str] = []
        with self._lock:
            for speaker, parts in self._pending.items():
                if not parts:
                    continue
                if force or now - self._last_delta_at[speaker] >= TRANSCRIPT_FLUSH_IDLE_S:
                    text = "".join(parts).strip()
                    self._pending[speaker] = []
                    if text:
                        lines.append(f"{speaker}: {text}")
        return lines


def _default_connect(url: str, headers: dict[str, str]) -> Any:
    from websockets.sync.client import connect

    return connect(url, additional_headers=headers, max_size=None, open_timeout=15)


class LiveSession:
    """Same surface as RealtimeSession so the worker protocol and HUD stay unchanged."""

    def __init__(
        self,
        *,
        policy: str,
        pyautogui: Any,
        emit: Emit,
        meter: Any | None,
        cancel: threading.Event,
        model: str | None = None,
        voice: str | None = None,
        noise_reduction: str | None = None,  # Live has no NR knob; accepted for protocol parity.
        connect: Connect | None = None,
    ) -> None:
        self.policy = policy
        self.pyautogui = pyautogui
        self.emit = emit
        self.meter = meter
        self.cancel = cancel
        self.model = model or os.environ.get("GOD_LIVE_MODEL") or DEFAULT_MODEL
        self.voice = voice or os.environ.get("GOD_LIVE_VOICE") or DEFAULT_VOICE
        self._connect = connect or _default_connect

        self._ws: Any | None = None
        self._send_lock = threading.Lock()
        self._event_ids = itertools.count(1)
        self._thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._closed = threading.Event()

        self._transcripts = TranscriptLog()
        self._known_delegations: set[str] = set()
        self._active_task: str | None = None
        self._cancel_reason: str | None = None
        self._cancel_reply_to: str | None = None
        self._typed_ids = itertools.count(1)

        self._last_out_audio_at = 0.0
        self._burst_started_at = 0.0
        self._burst_audio_ms = 0.0
        self._mouse_sustain = 0
        self._live_seconds_base = meter.live_seconds if meter is not None and hasattr(meter, "live_seconds") else 0.0

        self._desktop = self._make_runner()

    def _make_runner(self) -> DesktopTaskRunner:
        return DesktopTaskRunner(
            pyautogui=self.pyautogui,
            emit=self.emit,
            policy=self.policy,
            meter=self.meter,
            on_complete=self._on_desktop_complete,
        )

    # ── lifecycle ───────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Live session already running.")
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")
        self._stop.clear()
        self._started.clear()
        self._closed.clear()
        self.cancel.clear()
        self._desktop = self._make_runner()
        self._thread = threading.Thread(target=self._run, name="god-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.cancel.set()
        self._desktop.stop_join(timeout=2.0)
        if self._started.is_set() and not self._closed.is_set():
            # Docs: send session.close and keep reading until session.closed for final usage.
            self._send({"type": "session.close"})
            self._closed.wait(timeout=5.0)
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._watch_thread is not None:
            self._watch_thread.join(timeout=1.0)
            self._watch_thread = None

    def _run(self) -> None:
        key = os.environ["OPENAI_API_KEY"].strip()
        self.emit({"type": "agent_event", "level": "info", "message": f"Live connecting ({self.model})…"})
        try:
            ws = self._connect(LIVE_URL, {"Authorization": f"Bearer {key}"})
            self._ws = ws
            self._send(build_session_start(self.policy, model=self.model, voice=self.voice))
            for raw in ws:
                if self._handle_raw(raw) is False:
                    break
        except Exception as error:  # noqa: BLE001
            if not self._stop.is_set():
                self.emit({"type": "agent_event", "level": "error", "message": f"Live ended: {error}"})
        finally:
            self._ws = None
            self._started.clear()
            for line in self._transcripts.flush(force=True):
                self.emit({"type": "agent_event", "level": "info", "message": line})
            self.emit({"type": "agent_event", "level": "info", "message": "Live session closed."})

    # ── outbound ────────────────────────────────────────────────────────

    def _send(self, event: dict[str, Any]) -> bool:
        ws = self._ws
        if ws is None:
            return False
        event.setdefault("event_id", f"god_{next(self._event_ids)}")
        try:
            with self._send_lock:
                ws.send(json.dumps(event))
            return True
        except Exception as error:  # noqa: BLE001
            if not self._stop.is_set():
                self.emit({"type": "agent_event", "level": "error", "message": f"live send {event.get('type')}: {error}"})
            return False

    def _append(self, kind: str, content: str, delegation_id: str | None) -> None:
        """kind: commentary (spoken) | thinking (silent) | instructions."""
        did = delegation_id if delegation_id in self._known_delegations else None
        self._send({"type": f"session.{kind}.append", "delegation_id": did, "content": clip(content)})

    def append_audio(self, audio_b64: str) -> None:
        # Docs: no audio before session.started.
        if not audio_b64 or self._stop.is_set() or not self._started.is_set():
            return
        self._send({"type": "session.input_audio.append", "audio": audio_b64})

    def send_text(self, text: str, *, create_response: bool = True) -> None:  # noqa: ARG002
        """Typed input goes straight to the backend (client delegation); voice gets a silent mirror."""
        text = (text or "").strip()
        if not text:
            return
        task_id = f"typed-{next(self._typed_ids)}"
        self._start_task(task_id, text)
        self._append("thinking", f"The operator typed a desktop request: {text}. It is running; not finished yet.", None)

    def update_audio_profile(self, noise_reduction: str | None) -> str:
        # Live manages turn-taking itself; nothing to tune server-side.
        return (noise_reduction or "live").strip() or "live"

    # ── inbound ─────────────────────────────────────────────────────────

    def _handle_raw(self, raw: Any) -> bool | None:
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return self.handle_event(event)

    def handle_event(self, event: dict[str, Any]) -> bool | None:
        etype = event.get("type")

        if etype == "session.started":
            self._started.set()
            self.emit({"type": "agent_event", "level": "ok", "message": "Live session open — talk to God when you need it."})
            if self._watch_thread is None or not self._watch_thread.is_alive():
                self._watch_thread = threading.Thread(target=self._watch, name="god-live-watch", daemon=True)
                self._watch_thread.start()
            return None

        if etype == "session.output_audio.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                self._note_output_audio(delta)
                self.emit({"type": "speak", "audio_base64": delta, "format": "pcm16", "sample_rate": 24000, "stream": True})
            return None

        if etype == "session.output_transcript.delta":
            self._transcripts.add("God", str(event.get("delta") or ""), event.get("start_ms"))
            return None

        if etype == "session.input_transcript.delta":
            self._transcripts.add("You", str(event.get("delta") or ""), event.get("start_ms"))
            self._maybe_flush_playback()
            return None

        if etype == "session.delegation.created":
            delegation = event.get("delegation") or {}
            did = delegation.get("id")
            if isinstance(did, str) and delegation.get("target", "client") == "client":
                self._known_delegations.add(did)
                offset = event.get("offset_ms")
                offset_ms = int(offset) if isinstance(offset, (int, float)) else None
                timer = threading.Timer(DELEGATION_TRANSCRIPT_WAIT_S, self._dispatch_delegation, args=(did, offset_ms))
                timer.daemon = True
                timer.start()
            return None

        if etype == "session.usage.updated":
            seconds = (event.get("usage") or {}).get("seconds")
            if self.meter is not None and isinstance(seconds, (int, float)) and hasattr(self.meter, "set_live_seconds"):
                self.emit(self.meter.set_live_seconds(seconds, base=self._live_seconds_base))
            return None

        if etype == "error":
            err = event.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            self.emit({"type": "agent_event", "level": "error", "message": f"Live error: {message}"})
            return None

        if etype == "session.closed":
            seconds = (event.get("usage") or {}).get("seconds")
            if self.meter is not None and isinstance(seconds, (int, float)) and hasattr(self.meter, "set_live_seconds"):
                self.emit(self.meter.set_live_seconds(seconds, base=self._live_seconds_base))
            reason = event.get("reason") or "closed"
            if reason != "close_requested":
                self.emit({"type": "agent_event", "level": "info", "message": f"Live closed ({reason})."})
            self._closed.set()
            return False

        return None

    # ── playback / barge-in ─────────────────────────────────────────────

    def _note_output_audio(self, delta_b64: str) -> None:
        now = time.time()
        try:
            nbytes = len(base64.b64decode(delta_b64))
        except Exception:  # noqa: BLE001
            nbytes = 0
        if now - self._last_out_audio_at > 1.0:
            self._burst_started_at = now
            self._burst_audio_ms = 0.0
        self._burst_audio_ms += nbytes / PCM_BYTES_PER_MS
        self._last_out_audio_at = now

    def buffered_playback_ms(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        if self._burst_started_at <= 0:
            return 0.0
        return max(0.0, self._burst_audio_ms - (now - self._burst_started_at) * 1000.0)

    def _maybe_flush_playback(self, now: float | None = None) -> None:
        """Live stops generating when it yields to the user; drop audio the HUD already queued."""
        now = time.time() if now is None else now
        if now - self._last_out_audio_at < YIELD_GAP_S:
            return  # still generating → backchannel/noise, not a yield
        if self.buffered_playback_ms(now) < MIN_BUFFERED_MS_TO_FLUSH:
            return
        self._burst_started_at = 0.0
        self._burst_audio_ms = 0.0
        self.emit({"type": "realtime_interrupt", "reason": "speech"})

    # ── delegation → desktop runner ─────────────────────────────────────

    def _dispatch_delegation(self, delegation_id: str, offset_ms: int | None = None) -> None:
        if self._stop.is_set():
            return
        utterance = self._transcripts.take_user_goal(offset_ms)
        if not utterance:
            self._append("commentary", "I didn't catch what you want on the Mac. Could you say it again?", delegation_id)
            return
        if is_cancel_request(utterance):
            if self._desktop.is_running():
                self._cancel_reason = "operator"
                self._cancel_reply_to = delegation_id
                self._desktop.cancel()
                self._append("thinking", "Cancelling the desktop task. Not confirmed yet.", delegation_id)
            else:
                self._append("commentary", "Nothing is running on the Mac right now.", delegation_id)
            return
        self._start_task(delegation_id, utterance)
        self._append("thinking", f"Started desktop task: {utterance}. Not finished yet.", delegation_id)

    def _start_task(self, task_id: str, utterance: str) -> None:
        snap = self._desktop.snapshot()
        goal = utterance
        previous = snap.get("goal") or ""
        recent = snap.get("status") == "running" or time.time() - float(snap.get("finished_at") or 0) < 120
        if previous and recent:
            goal = (
                f"{utterance}\n\nContext: the previous desktop goal was: {previous}. "
                "If the new request is a correction of it, apply the correction."
            )
        self._active_task = task_id
        self._cancel_reason = None
        self._cancel_reply_to = None
        self.emit({"type": "agent_event", "level": "info", "message": f"Delegated: {utterance}"})
        result = self._desktop.replace(goal, task_id=task_id)
        if not result.get("ok"):
            self._append("commentary", f"I couldn't start that: {result.get('message')}", task_id)

    def _on_desktop_complete(self, payload: dict[str, Any]) -> None:
        if self._stop.is_set():
            return
        task_id = payload.get("task_id")
        if task_id != self._active_task:
            # Superseded by a correction — never speak an outdated result.
            return
        status = payload.get("status")
        result = str(payload.get("result") or "")
        if status == "done":
            self._append("commentary", f"Desktop task finished. Result: {result}", task_id)
        elif status == "error":
            self._append("commentary", f"The desktop task failed: {result}", task_id)
        elif status == "cancelled":
            if self._cancel_reason == "mouse":
                self._append(
                    "commentary",
                    "You took the mouse, so I stopped the desktop task. Tell me if you want me to carry on.",
                    task_id,
                )
            else:
                self._append("commentary", "Stopped. The desktop task is cancelled.", self._cancel_reply_to or task_id)
        self._cancel_reason = None
        self._cancel_reply_to = None

    # ── watcher: transcript flush + mouse takeover ──────────────────────

    def _watch(self) -> None:
        try:
            last = self.pyautogui.position()
        except Exception:  # noqa: BLE001
            last = None
        while not self._stop.is_set():
            time.sleep(WATCH_POLL_S)
            for line in self._transcripts.flush():
                self.emit({"type": "agent_event", "level": "info", "message": line})
            if last is None:
                continue
            try:
                pos = self.pyautogui.position()
            except Exception:  # noqa: BLE001
                continue
            moved = abs(int(pos.x) - int(last.x)) + abs(int(pos.y) - int(last.y)) >= MOUSE_MOVE_THRESHOLD_PX
            last = pos
            if not moved or is_agent_controlling_mouse() or not self._desktop.is_running():
                self._mouse_sustain = 0
                continue
            self._mouse_sustain += 1
            if self._mouse_sustain < MOUSE_SUSTAINED_POLLS:
                continue
            self._mouse_sustain = 0
            self._cancel_reason = "mouse"
            self._desktop.cancel()
            self.emit({"type": "agent_event", "level": "info", "message": "Mouse moved by operator — stopping desktop task."})
