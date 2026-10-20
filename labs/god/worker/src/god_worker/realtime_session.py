"""OpenAI Realtime API session — duplex voice + desktop handoff (tools run off-thread)."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from god_worker.desktop_task import DesktopTaskRunner
from god_worker.mouse import is_agent_controlling_mouse

Emit = Callable[[dict[str, Any]], None]

DEFAULT_MODEL = "gpt-realtime-2.1"
# Higher than before: ignore small bumps; require a clearer human nudge.
MOUSE_MOVE_THRESHOLD_PX = 64
MOUSE_INTERRUPT_COOLDOWN_S = 12.0
MOUSE_POLL_S = 0.15
# Need two consecutive polls over threshold (reduces one-frame false pauses).
MOUSE_SUSTAINED_POLLS = 2
# Laptop/room mics: higher = fewer false turns; too high and quiet speech is missed.
# 0.55–0.6 is the practical band for MacBook mics (docs default is 0.5).
DEFAULT_FAR_FIELD_VAD_THRESHOLD = 0.58


def build_instructions(policy: str, plan: str = "") -> str:
    plan_block = plan.strip() or "(desktop runner owns the detailed plan)"
    return "\n".join(
        [
            "You are God, a personal macOS desktop operator in a live voice conversation.",
            "Speak briefly and naturally. Remember everything said in this session.",
            "You can operate ANY macOS application via a BACKGROUND desktop runner.",
            "CRITICAL: Never try to click or type yourself. You only have voice-control tools:",
            "run_desktop_task(goal), cancel_desktop_task, get_desktop_status.",
            "Workflow for desktop work:",
            "1) Briefly tell the operator what you will do (one short sentence).",
            "2) Call run_desktop_task with a clear goal (app + what to do).",
            "3) Keep chatting — the runner works in the background; you stay listening.",
            "4) When a SYSTEM notice says the desktop task finished, summarize briefly.",
            "If they interrupt or change goals while a task runs: cancel_desktop_task, then start a new one.",
            "Always answer clearly when the operator speaks with a real utterance.",
            "If input is unclear, nonsense, a single random word, or likely background noise,",
            "ask them to repeat once. Do NOT invent meaning. Do NOT claim you resumed desktop work",
            "unless they clearly said continue or gave a real task.",
            "If told the operator moved the mouse, acknowledge pause; call cancel_desktop_task if needed,",
            "and wait for continue or a new task before run_desktop_task again.",
            f"Desktop status hint:\n{plan_block}",
            f"Operator policy:\n{policy.strip() or '(none — follow the user safely)'}",
        ]
    )


def build_realtime_tools() -> list[dict[str, Any]]:
    """Voice-control tools only — long GUI/exec work runs off-thread (see desktop_task)."""
    return [
        {
            "type": "function",
            "name": "run_desktop_task",
            "description": (
                "Start a BACKGROUND desktop agent for a goal (open apps, click, type, browse). "
                "Returns immediately. Keep talking; you will get a SYSTEM notice when it finishes. "
                "Speak a one-sentence announcement BEFORE calling this."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["goal"],
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Clear desktop goal, e.g. Open Safari and go to example.com",
                    }
                },
            },
        },
        {
            "type": "function",
            "name": "cancel_desktop_task",
            "description": "Cancel the running background desktop task, if any.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
        {
            "type": "function",
            "name": "get_desktop_status",
            "description": "Check whether a desktop task is idle, running, done, or cancelled.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
        },
    ]


def normalize_noise_reduction(value: str | None) -> str:
    """near_field = headphones/close mic; far_field = laptop/room mic (OpenAI Realtime)."""
    raw = (value or "").strip().lower().replace("-", "_")
    if raw in {"near_field", "nearfield", "near"}:
        return "near_field"
    if raw in {"far_field", "farfield", "far"}:
        return "far_field"
    # Safe default for MacBook built-in mic when the client does not specify.
    return "far_field"


def far_field_vad_threshold() -> float:
    raw = os.environ.get("GOD_VAD_THRESHOLD", "").strip()
    if not raw:
        return DEFAULT_FAR_FIELD_VAD_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_FAR_FIELD_VAD_THRESHOLD
    return min(0.95, max(0.1, value))


def build_turn_detection(noise_reduction: str) -> dict[str, Any]:
    """Pair NR mode with the VAD that fits the mic.

    Per OpenAI Realtime conversations guide: keep VAD for chunking, but set
    create_response=false so the client decides when to response.create — otherwise
    every noise-triggered VAD stop becomes a spoken "I didn't catch that" turn.
    """
    nr = normalize_noise_reduction(noise_reduction)
    if nr == "far_field":
        return {
            "type": "server_vad",
            "threshold": far_field_vad_threshold(),
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
            # Manual response.create after a clean transcript (see Realtime conversations).
            "create_response": False,
            # Don't cancel God mid-sentence on ambient VAD blips from the laptop mic.
            "interrupt_response": False,
        }
    return {
        "type": "semantic_vad",
        "eagerness": "low",
        "create_response": False,
        # Headset is clean enough for true barge-in.
        "interrupt_response": True,
    }


def is_noise_like_transcript(text: str) -> bool:
    """Heuristic for ASR garbage (random CJK, tiny fragments) — not a real English command."""
    raw = (text or "").strip()
    if not raw:
        return True
    # Mostly non-Latin letters (e.g. "這是") → treat as noise for control-plane decisions.
    letters = [ch for ch in raw if ch.isalpha()]
    if letters:
        latin = sum(1 for ch in letters if ("a" <= ch.lower() <= "z"))
        if latin / max(1, len(letters)) < 0.5:
            return True
    words = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in raw.lower()).split()
    if len(words) <= 1 and len(raw) <= 8:
        # Single short token like "Squid." / "Hi." — too weak to drive tool resume.
        if words and words[0] not in {"yes", "ok", "okay", "sure", "yep", "yeah", "continue"}:
            return True
    return False


def should_resume_from_transcript(text: str) -> bool:
    """Resume paused tools only on clear operator intent — not ASR noise fragments."""
    if is_noise_like_transcript(text):
        return False
    compact = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in (text or "").lower())
    words = compact.split()
    if not words:
        return False
    joined = " ".join(words)
    if joined in {"yes", "ok", "okay", "sure", "yep", "yeah"} or (
        len(words) == 1 and words[0] in {"yes", "ok", "okay", "sure", "yep", "yeah"}
    ):
        return True
    resume_phrases = (
        "continue",
        "go ahead",
        "keep going",
        "resume",
        "carry on",
        "unpause",
    )
    if any(p in joined for p in resume_phrases):
        return True
    # Clear new task: real cue + enough words (rejects "i was miami" / "squid").
    task_cues = (
        "open ",
        "click ",
        "type ",
        "please ",
        "can you",
        "could you",
        "i want",
        "go to",
        "launch ",
        "search ",
        "new task",
    )
    if len(words) >= 4 and any(c in joined for c in task_cues):
        return True
    return False


def session_config(
    policy: str,
    model: str,
    voice: str,
    plan: str = "",
    *,
    noise_reduction: str = "far_field",
) -> dict[str, Any]:
    nr = normalize_noise_reduction(noise_reduction)
    return {
        "type": "realtime",
        "model": model,
        "output_modalities": ["audio"],
        "instructions": build_instructions(policy, plan=plan),
        "tools": build_realtime_tools(),
        "tool_choice": "auto",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "noise_reduction": {"type": nr},
                "turn_detection": build_turn_detection(nr),
                "transcription": {"model": "gpt-4o-mini-transcribe", "language": "en"},
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "voice": voice,
            },
        },
    }


class RealtimeSession:
    """Stateful Realtime WebSocket session owned by the worker process."""

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
        noise_reduction: str | None = None,
    ) -> None:
        self.policy = policy
        self.pyautogui = pyautogui
        self.emit = emit
        self.meter = meter
        self.cancel = cancel
        self.model = model or os.environ.get("GOD_REALTIME_MODEL") or DEFAULT_MODEL
        self.voice = voice or os.environ.get("GOD_REALTIME_VOICE") or "marin"
        self.noise_reduction = normalize_noise_reduction(
            noise_reduction or os.environ.get("GOD_REALTIME_NOISE_REDUCTION")
        )

        self._thread: threading.Thread | None = None
        self._mouse_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connection: Any | None = None
        self._conn_lock = threading.Lock()
        self._awaiting_continue = False
        self._last_interrupt_at = 0.0
        self._paused = False
        self._current_item_id: str | None = None
        self._audio_played_ms = 0
        self._actions_frozen = False
        self._mouse_sustain = 0
        self._desktop = DesktopTaskRunner(
            pyautogui=pyautogui,
            emit=emit,
            policy=policy,
            meter=meter,
            on_complete=self._on_desktop_complete,
        )

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Realtime session already running.")
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")

        self._actions_frozen = False
        self._paused = False
        self._awaiting_continue = False
        self._mouse_sustain = 0
        self._desktop = DesktopTaskRunner(
            pyautogui=self.pyautogui,
            emit=self.emit,
            policy=self.policy,
            meter=self.meter,
            on_complete=self._on_desktop_complete,
        )
        self._stop.clear()
        self.cancel.clear()
        self._thread = threading.Thread(target=self._run, name="god-realtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.cancel.set()
        self._desktop.stop_join(timeout=2.0)
        with self._conn_lock:
            conn = self._connection
            self._connection = None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._mouse_thread is not None:
            self._mouse_thread.join(timeout=1.0)
            self._mouse_thread = None

    def append_audio(self, audio_b64: str) -> None:
        if not audio_b64 or self._stop.is_set():
            return
        with self._conn_lock:
            conn = self._connection
        if conn is None:
            return
        try:
            conn.input_audio_buffer.append(audio=audio_b64)
        except Exception as error:  # noqa: BLE001
            self.emit({"type": "agent_event", "level": "error", "message": f"audio_in: {error}"})

    def send_text(self, text: str, *, create_response: bool = True) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._conn_lock:
            conn = self._connection
        if conn is None:
            return
        conn.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        if create_response:
            conn.response.create()

    def update_audio_profile(self, noise_reduction: str | None) -> str:
        """Hot-swap NR + VAD when the operator changes mic (AirPods ↔ MacBook)."""
        nr = normalize_noise_reduction(noise_reduction)
        if nr == self.noise_reduction:
            return nr
        self.noise_reduction = nr
        turn = build_turn_detection(nr)
        with self._conn_lock:
            conn = self._connection
        if conn is None:
            return nr
        try:
            conn.session.update(
                session={
                    "type": "realtime",
                    "audio": {
                        "input": {
                            "noise_reduction": {"type": nr},
                            "turn_detection": turn,
                        }
                    },
                }
            )
            vad = turn.get("type", "?")
            detail = f"Audio profile → {nr} + {vad}"
            if vad == "server_vad":
                detail += f" (threshold={turn.get('threshold')})"
            self.emit({"type": "agent_event", "level": "info", "message": detail})
        except Exception as error:  # noqa: BLE001
            self.emit(
                {
                    "type": "agent_event",
                    "level": "error",
                    "message": f"audio profile update failed: {error}",
                }
            )
        return nr

    def _desktop_status_hint(self) -> str:
        snap = self._desktop.snapshot()
        status = snap.get("status", "idle")
        goal = snap.get("goal") or ""
        if status == "running" and goal:
            return f"running: {goal[:120]}"
        if status in {"done", "error", "cancelled"} and snap.get("last_result"):
            return f"{status}: {str(snap['last_result'])[:120]}"
        return str(status)

    def _refresh_session_instructions(self, connection: Any) -> None:
        instructions = build_instructions(self.policy, plan=self._desktop_status_hint())
        try:
            connection.session.update(
                session={
                    "type": "realtime",
                    "instructions": instructions,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_desktop_complete(self, payload: dict[str, Any]) -> None:
        if self._stop.is_set():
            return
        status = payload.get("status", "done")
        result = str(payload.get("result") or "")
        goal = str(payload.get("goal") or "")
        notice = (
            f"SYSTEM NOTICE: Desktop task {status}. Goal was: {goal[:200]}. "
            f"Result: {result[:400]}. Tell the operator briefly what happened. "
            "Do not call run_desktop_task again unless they ask."
        )
        try:
            self.send_text(notice, create_response=True)
            with self._conn_lock:
                conn = self._connection
            if conn is not None:
                self._refresh_session_instructions(conn)
        except Exception as error:  # noqa: BLE001
            self.emit(
                {
                    "type": "agent_event",
                    "level": "error",
                    "message": f"desktop complete notify failed: {error}",
                }
            )

    def _dispatch_voice_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "run_desktop_task":
            if self._paused or self._awaiting_continue or self._actions_frozen:
                return {
                    "ok": False,
                    "started": False,
                    "paused": True,
                    "message": "Desktop actions paused — waiting for operator to continue.",
                }
            goal = args.get("goal") if isinstance(args, dict) else ""
            return self._desktop.start(str(goal or ""))
        if name == "cancel_desktop_task":
            return self._desktop.cancel()
        if name == "get_desktop_status":
            return self._desktop.snapshot()
        return {"ok": False, "message": f"Unknown tool: {name}"}

    def _run(self) -> None:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())
        self.emit(
            {
                "type": "agent_event",
                "level": "info",
                "message": f"Realtime connecting ({self.model})…",
            }
        )
        try:
            with client.realtime.connect(model=self.model) as connection:
                with self._conn_lock:
                    self._connection = connection
                connection.session.update(
                    session=session_config(
                        self.policy,
                        self.model,
                        self.voice,
                        plan=self._desktop_status_hint(),
                        noise_reduction=self.noise_reduction,
                    )
                )
                turn = build_turn_detection(self.noise_reduction)
                vad_msg = f"Realtime audio={self.noise_reduction} + {turn.get('type')}"
                if turn.get("type") == "server_vad":
                    vad_msg += f" threshold={turn.get('threshold')}"
                self.emit({"type": "agent_event", "level": "info", "message": vad_msg})
                if self._mouse_thread is None or not self._mouse_thread.is_alive():
                    self._mouse_thread = threading.Thread(
                        target=self._mouse_watch, name="god-mouse-watch", daemon=True
                    )
                    self._mouse_thread.start()
                self.emit(
                    {
                        "type": "agent_event",
                        "level": "ok",
                        "message": "Realtime session open — speak naturally.",
                    }
                )
                connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "System: Session started. Greet the operator briefly "
                                    "and say you can operate any Mac app with visible mouse control."
                                ),
                            }
                        ],
                    }
                )
                connection.response.create()

                for event in connection:
                    if self._stop.is_set() or self.cancel.is_set():
                        break
                    self._handle_event(event, connection)
        except Exception as error:  # noqa: BLE001
            if not self._stop.is_set():
                self.emit(
                    {
                        "type": "agent_event",
                        "level": "error",
                        "message": f"Realtime ended: {error}",
                    }
                )
        finally:
            with self._conn_lock:
                self._connection = None
            self.emit({"type": "agent_event", "level": "info", "message": "Realtime session closed."})

    def _handle_event(self, event: Any, connection: Any) -> None:
        etype = getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else None)
        if not etype:
            return

        if etype == "error":
            message = getattr(event, "message", None) or str(event)
            self.emit({"type": "agent_event", "level": "error", "message": f"Realtime error: {message}"})
            return

        if etype == "input_audio_buffer.speech_started":
            # Only barge-in locally when server interrupt_response is enabled (headset profile).
            if build_turn_detection(self.noise_reduction).get("interrupt_response"):
                self.emit({"type": "realtime_interrupt", "reason": "speech"})
                self._audio_played_ms = 0
            return

        if etype == "response.output_audio.delta":
            delta = getattr(event, "delta", None)
            item_id = getattr(event, "item_id", None)
            if item_id:
                self._current_item_id = item_id
            if isinstance(delta, str) and delta:
                try:
                    import base64

                    nbytes = len(base64.b64decode(delta))
                    self._audio_played_ms += max(1, nbytes // 48)
                except Exception:  # noqa: BLE001
                    pass
                self.emit(
                    {
                        "type": "speak",
                        "audio_base64": delta,
                        "format": "pcm16",
                        "sample_rate": 24000,
                        "stream": True,
                    }
                )
            return

        if etype == "response.output_audio_transcript.delta":
            delta = getattr(event, "delta", None)
            if isinstance(delta, str) and delta.strip():
                self.emit({"type": "agent_event", "level": "info", "message": delta, "partial": True})
            return

        if etype == "response.output_audio_transcript.done":
            transcript = getattr(event, "transcript", None)
            if isinstance(transcript, str) and transcript.strip():
                self.emit({"type": "agent_event", "level": "ok", "message": f"God: {transcript.strip()}"})
            return

        if etype == "conversation.item.input_audio_transcription.completed":
            transcript = getattr(event, "transcript", None)
            if isinstance(transcript, str) and transcript.strip():
                text = transcript.strip()
                if is_noise_like_transcript(text):
                    self.emit(
                        {
                            "type": "agent_event",
                            "level": "info",
                            "message": f"(ignored noise-like input: {text})",
                        }
                    )
                    return
                self.emit({"type": "agent_event", "level": "info", "message": f"You: {text}"})
                # Transcription is guidance only — resume tools solely on clear intent.
                if (self._awaiting_continue or self._paused) and should_resume_from_transcript(text):
                    self._awaiting_continue = False
                    self._paused = False
                    self._actions_frozen = False
                    self._mouse_sustain = 0
                    self.emit(
                        {
                            "type": "agent_event",
                            "level": "info",
                            "message": "Resumed after operator speech.",
                        }
                    )
                # Docs pattern: VAD chunks audio; client triggers response only for real speech.
                try:
                    connection.response.create()
                except Exception as error:  # noqa: BLE001
                    self.emit(
                        {
                            "type": "agent_event",
                            "level": "error",
                            "message": f"response.create failed: {error}",
                        }
                    )
            return

        if etype == "response.done":
            self._on_response_done(event, connection)
            return

    def _on_response_done(self, event: Any, connection: Any) -> None:
        response = getattr(event, "response", None)
        if response is None and isinstance(event, dict):
            response = event.get("response")
        if response is None:
            return

        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if self.meter is not None and usage is not None:
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump(exclude_none=True)
            elif not isinstance(usage, dict):
                usage = {
                    "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                }
            self.emit(self.meter.add_response_usage(usage))

        output = getattr(response, "output", None)
        if output is None and isinstance(response, dict):
            output = response.get("output")
        if not output:
            return

        calls: list[dict[str, Any]] = []
        for item in output:
            itype = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
            if itype != "function_call":
                continue
            name = getattr(item, "name", None) or item.get("name")
            call_id = getattr(item, "call_id", None) or item.get("call_id")
            arguments = getattr(item, "arguments", None) or item.get("arguments") or "{}"
            calls.append({"name": name, "call_id": call_id, "arguments": arguments})

        if not calls:
            return

        for call in calls:
            if self.cancel.is_set() or self._stop.is_set():
                return
            name = str(call["name"] or "")
            try:
                args_obj = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
            except json.JSONDecodeError:
                args_obj = {}
            if not isinstance(args_obj, dict):
                args_obj = {}

            # cancel always allowed; start/status respect pause freeze
            if name == "cancel_desktop_task":
                result = self._dispatch_voice_tool(name, args_obj)
            elif self._paused or self._awaiting_continue or self._actions_frozen:
                result = {
                    "ok": False,
                    "paused": True,
                    "message": "Desktop actions paused — waiting for operator to continue.",
                }
            else:
                result = self._dispatch_voice_tool(name, args_obj)

            connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(result, ensure_ascii=False),
                }
            )
            if name in {"run_desktop_task", "cancel_desktop_task"}:
                self._refresh_session_instructions(connection)

        if not self.cancel.is_set() and not self._stop.is_set():
            connection.response.create()

    def _mouse_watch(self) -> None:
        try:
            last = self.pyautogui.position()
        except Exception:  # noqa: BLE001
            return
        while not self._stop.is_set() and not self.cancel.is_set():
            time.sleep(MOUSE_POLL_S)
            try:
                pos = self.pyautogui.position()
            except Exception:  # noqa: BLE001
                continue
            dx = abs(int(pos.x) - int(last.x))
            dy = abs(int(pos.y) - int(last.y))
            moved = dx + dy >= MOUSE_MOVE_THRESHOLD_PX
            last = pos
            if not moved:
                self._mouse_sustain = 0
                continue
            # Agent-owned motion (visible mouse / tool section) must never look like the operator.
            if is_agent_controlling_mouse():
                self._mouse_sustain = 0
                continue
            # Only pause when a desktop task is actually driving the screen.
            if not self._desktop.is_running():
                self._mouse_sustain = 0
                continue
            self._mouse_sustain += 1
            if self._mouse_sustain < MOUSE_SUSTAINED_POLLS:
                continue
            now = time.time()
            if now - self._last_interrupt_at < MOUSE_INTERRUPT_COOLDOWN_S:
                continue
            if self._awaiting_continue:
                continue
            self._mouse_sustain = 0
            self._last_interrupt_at = now
            self._on_user_mouse_moved()

    def _on_user_mouse_moved(self) -> None:
        self._paused = True
        self._awaiting_continue = True
        # Stop the background runner so Realtime stays free and the cursor is free.
        self._desktop.cancel()
        self.emit(
            {
                "type": "agent_event",
                "level": "info",
                "message": "Mouse moved by operator — pausing desktop task.",
            }
        )
        # Do not emit realtime_interrupt here — that chopped live speech mid-sentence.
        try:
            self.send_text(
                "SYSTEM NOTICE: The operator just moved the mouse themselves. "
                "Desktop task was cancelled. Keep talking normally. "
                "Briefly acknowledge you paused until they say continue or give a new task. "
                "Do not call run_desktop_task until they resume.",
                create_response=True,
            )
        except Exception as error:  # noqa: BLE001
            self.emit(
                {
                    "type": "agent_event",
                    "level": "error",
                    "message": f"mouse interrupt failed: {error}",
                }
            )
