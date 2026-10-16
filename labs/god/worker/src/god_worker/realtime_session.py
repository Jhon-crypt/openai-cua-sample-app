"""OpenAI Realtime API session — duplex voice, tools, memory, mouse interrupt."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from god_worker.agent_loop import build_code_tool_definitions, execute_code

Emit = Callable[[dict[str, Any]], None]

DEFAULT_MODEL = "gpt-realtime-2.1"
MOUSE_MOVE_THRESHOLD_PX = 14
MOUSE_INTERRUPT_COOLDOWN_S = 5.0
MOUSE_POLL_S = 0.08


def build_instructions(policy: str) -> str:
    return "\n".join(
        [
            "You are God, a personal macOS desktop operator in a live voice conversation.",
            "Speak briefly and naturally. Remember everything said in this session.",
            "When asked to act on the Mac, call exec_py with PyAutoGUI.",
            "To open apps, always use open_app('AppName') — never Spotlight.",
            "After open_app, sleep briefly, screenshot, then type or click only if the UI is visible.",
            "Mouse moves are intentionally slow so the operator can see them.",
            "If you are told the operator moved the mouse, pause desktop work immediately,",
            "say: 'Seems you moved your mouse — tell me when to continue with what you're doing.',",
            "and wait until they say continue (or give a new instruction) before acting again.",
            "Obey the operator policy exactly when present.",
            f"Operator policy:\n{policy.strip() or '(none — follow the user safely)'}",
        ]
    )


def build_realtime_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in build_code_tool_definitions():
        tools.append({k: v for k, v in tool.items() if k != "strict"})
    return tools


def session_config(policy: str, model: str, voice: str) -> dict[str, Any]:
    return {
        "type": "realtime",
        "model": model,
        "output_modalities": ["audio"],
        "instructions": build_instructions(policy),
        "tools": build_realtime_tools(),
        "tool_choice": "auto",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "turn_detection": {"type": "semantic_vad"},
                "transcription": {"model": "gpt-4o-mini-transcribe"},
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
    ) -> None:
        self.policy = policy
        self.pyautogui = pyautogui
        self.emit = emit
        self.meter = meter
        self.cancel = cancel
        self.model = model or os.environ.get("GOD_REALTIME_MODEL") or DEFAULT_MODEL
        self.voice = voice or os.environ.get("GOD_REALTIME_VOICE") or "marin"

        self._thread: threading.Thread | None = None
        self._mouse_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connection: Any | None = None
        self._conn_lock = threading.Lock()
        self._agent_moving_until = 0.0
        self._awaiting_continue = False
        self._last_interrupt_at = 0.0
        self._paused = False
        self._current_item_id: str | None = None
        self._audio_played_ms = 0
        self._namespace: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Realtime session already running.")
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")

        import time as time_mod

        from god_worker.desktop import open_app

        self._namespace = {"pyautogui": self.pyautogui, "time": time_mod, "open_app": open_app}
        self._wrap_agent_moves()
        self._stop.clear()
        self.cancel.clear()
        self._thread = threading.Thread(target=self._run, name="god-realtime", daemon=True)
        self._thread.start()
        self._mouse_thread = threading.Thread(
            target=self._mouse_watch, name="god-mouse-watch", daemon=True
        )
        self._mouse_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.cancel.set()
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
            raise RuntimeError("Realtime session is not connected.")
        conn.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        if create_response:
            conn.response.create()

    def _wrap_agent_moves(self) -> None:
        original = self.pyautogui.moveTo

        def move_to(*args: Any, **kwargs: Any) -> Any:
            duration = float(kwargs.get("duration", 0.45) or 0.45)
            self._agent_moving_until = time.time() + duration + 0.35
            return original(*args, **kwargs)

        self.pyautogui.moveTo = move_to

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
                    session=session_config(self.policy, self.model, self.voice)
                )
                self.emit(
                    {
                        "type": "agent_event",
                        "level": "ok",
                        "message": "Realtime session open — speak naturally.",
                    }
                )
                # Greeting so the operator hears the voice path immediately.
                connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "System: Session started. Greet the operator briefly "
                                    "and say you are ready to talk and help on their Mac."
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
            self.emit({"type": "realtime_interrupt", "reason": "speech"})
            self._audio_played_ms = 0
            return

        if etype == "response.output_audio.delta":
            delta = getattr(event, "delta", None)
            item_id = getattr(event, "item_id", None)
            if item_id:
                self._current_item_id = item_id
            if isinstance(delta, str) and delta:
                # 24 kHz mono pcm16 → 48 bytes/ms
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
                self.emit({"type": "agent_event", "level": "info", "message": f"You: {text}"})
                lower = text.lower()
                if self._awaiting_continue and any(
                    w in lower for w in ("continue", "go ahead", "keep going", "resume", "carry on")
                ):
                    self._awaiting_continue = False
                    self._paused = False
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
            if name != "exec_py":
                connection.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({"error": f"Unknown tool: {name}"}),
                    }
                )
                continue
            calls.append({"call_id": call_id, "arguments": arguments})

        if not calls:
            return

        if self._paused or self._awaiting_continue:
            for call in calls:
                connection.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(
                            {
                                "paused": True,
                                "message": "Operator moved the mouse; waiting for them to say continue.",
                            }
                        ),
                    }
                )
            connection.response.create()
            return

        for call in calls:
            if self.cancel.is_set() or self._stop.is_set():
                return
            try:
                args = json.loads(call["arguments"])
                code = args.get("code") if isinstance(args, dict) else None
                if not isinstance(code, str) or not code.strip():
                    raise RuntimeError("exec_py requires nonempty code.")
                self.emit(
                    {
                        "type": "agent_event",
                        "level": "pending",
                        "message": f"exec_py: {code[:180]}{'…' if len(code) > 180 else ''}",
                    }
                )
                if self._paused:
                    raise RuntimeError("Paused — operator moved the mouse.")
                output_parts = execute_code(code, self._namespace)
                # Realtime function output is a string; summarize multimodal parts.
                text_bits: list[str] = []
                for part in output_parts:
                    if part.get("type") == "input_text":
                        text_bits.append(str(part.get("text", "")))
                    elif part.get("type") == "input_image":
                        text_bits.append("[screenshot attached as base64 image in local session]")
                        # Also push image into conversation memory as a user note (text summary).
                result = "\n".join(text_bits) or "exec_py completed."
            except Exception as error:  # noqa: BLE001
                result = f"exec_py error: {error}"
                self.emit({"type": "agent_event", "level": "error", "message": str(error)})

            connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": result,
                }
            )

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
                continue
            if time.time() < self._agent_moving_until:
                continue
            now = time.time()
            if now - self._last_interrupt_at < MOUSE_INTERRUPT_COOLDOWN_S:
                continue
            if self._awaiting_continue:
                continue
            self._last_interrupt_at = now
            self._on_user_mouse_moved()

    def _on_user_mouse_moved(self) -> None:
        self._paused = True
        self._awaiting_continue = True
        self.emit(
            {
                "type": "agent_event",
                "level": "info",
                "message": "Mouse moved by operator — pausing actions.",
            }
        )
        self.emit({"type": "realtime_interrupt", "reason": "mouse"})
        try:
            self.send_text(
                "SYSTEM NOTICE: The operator just moved the mouse themselves. "
                "Stop any desktop actions. Say aloud: "
                "'Seems you moved your mouse — tell me when to continue with what you're doing.' "
                "Do not call tools until they say continue or give a new instruction.",
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
