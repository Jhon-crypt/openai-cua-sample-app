"""OpenAI Realtime API session — duplex voice, GUI tools, plan, context, stuck, mouse interrupt."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from god_worker.context_manager import ContextManager
from god_worker.gui_tools import (
    DesktopHarness,
    build_gui_tool_definitions,
    extract_verify_image,
    tool_result_for_model,
)
from god_worker.stuck import StuckDetector

Emit = Callable[[dict[str, Any]], None]

DEFAULT_MODEL = "gpt-realtime-2.1"
MOUSE_MOVE_THRESHOLD_PX = 36
MOUSE_INTERRUPT_COOLDOWN_S = 10.0
MOUSE_POLL_S = 0.15


def build_instructions(policy: str, plan: str = "") -> str:
    plan_block = plan.strip() or "(no plan yet — call update_plan for multi-step goals)"
    return "\n".join(
        [
            "You are God, a personal macOS desktop operator in a live voice conversation.",
            "Speak briefly and naturally. Remember everything said in this session.",
            "You can operate ANY macOS application (Safari, Slack, Notes, Finder, Terminal,",
            "Chrome, Mail, Calendar, Xcode, etc.).",
            "Prefer structured tools: open_app, screenshot, click_text, click_element, click, type_text, hotkey, update_plan.",
            "For labeled UI (Chrome profiles, buttons with text): use click_text(query) first.",
            "For native Mac controls: try click_element(query). Fall back to screenshot + click(x,y).",
            "click(x,y) coordinates are in the LATEST screenshot pixel space (img_w×img_h); the harness maps to the real screen.",
            "Use exec_py only for unusual automation that those tools cannot do.",
            "Never use Spotlight. Always open_app('ExactAppName') (e.g. Google Chrome).",
            "Workflow: update_plan → open_app → click_text/click_element (or screenshot+click) → trust auto-verify.",
            "Mouse moves are slow so the operator can watch the red cursor ring.",
            "Always answer the operator when they speak — conversation must never go silent.",
            "If told the operator moved the mouse, pause only DESKTOP tools (clicks/typing), still chat normally,",
            "and say briefly that you paused actions until they say continue or give a new task.",
            "Any new spoken instruction (not only the word continue) may resume desktop work.",
            "If stuck-detection says to change approach, do not repeat the same failing click.",
            f"Current plan:\n{plan_block}",
            f"Operator policy:\n{policy.strip() or '(none — follow the user safely)'}",
        ]
    )


def build_realtime_tools() -> list[dict[str, Any]]:
    return build_gui_tool_definitions(include_exec_py=True)


def session_config(policy: str, model: str, voice: str, plan: str = "") -> dict[str, Any]:
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
                "noise_reduction": {"type": "far_field"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "low",
                    "create_response": True,
                    "interrupt_response": True,
                },
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
        self._harness: DesktopHarness | None = None
        self._context = ContextManager()
        self._stuck = StuckDetector()
        self._actions_frozen = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Realtime session already running.")
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")

        self._harness = DesktopHarness(self.pyautogui, emit=self.emit)
        self._context = ContextManager()
        self._stuck = StuckDetector()
        self._actions_frozen = False
        self._wrap_agent_moves()
        self._stop.clear()
        self.cancel.clear()
        self._thread = threading.Thread(target=self._run, name="god-realtime", daemon=True)
        self._thread.start()

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

    def _wrap_agent_moves(self) -> None:
        original = self.pyautogui.moveTo

        def move_to(*args: Any, **kwargs: Any) -> Any:
            duration = float(kwargs.get("duration", 0.65) or 0.65)
            self._agent_moving_until = time.time() + duration + 0.35
            return original(*args, **kwargs)

        self.pyautogui.moveTo = move_to

    def _refresh_session_instructions(self, connection: Any) -> None:
        plan = self._harness.plan if self._harness else ""
        ctx = self._context.prompt_block()
        instructions = build_instructions(self.policy, plan=plan)
        if ctx:
            instructions = instructions + "\n\n" + ctx
        try:
            connection.session.update(
                session={
                    "type": "realtime",
                    "instructions": instructions,
                }
            )
        except Exception:  # noqa: BLE001
            pass

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
                plan = self._harness.plan if self._harness else ""
                connection.session.update(
                    session=session_config(self.policy, self.model, self.voice, plan=plan)
                )
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
                self.emit({"type": "agent_event", "level": "info", "message": f"You: {text}"})
                lower = text.lower()
                # Any real utterance resumes chat+tools so the operator is never stuck muted
                # after an accidental mouse bump. Short acks still count.
                if self._awaiting_continue or self._paused:
                    resume_words = (
                        "continue",
                        "go ahead",
                        "keep going",
                        "resume",
                        "carry on",
                        "open",
                        "click",
                        "type",
                        "please",
                        "can you",
                        "could you",
                        "do ",
                        "start",
                        "stop",
                        "yes",
                        "ok",
                        "okay",
                    )
                    if len(text) >= 2 and (
                        any(w in lower for w in resume_words) or len(text.split()) >= 2
                    ):
                        self._awaiting_continue = False
                        self._paused = False
                        self._actions_frozen = False
                        self.emit(
                            {
                                "type": "agent_event",
                                "level": "info",
                                "message": "Resumed after operator speech.",
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

        if self._paused or self._awaiting_continue or self._actions_frozen:
            for call in calls:
                connection.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(
                            {
                                "paused": True,
                                "message": "Desktop actions paused — waiting for operator to continue.",
                            }
                        ),
                    }
                )
            connection.response.create()
            return

        harness = self._harness
        if harness is None:
            return

        for call in calls:
            if self.cancel.is_set() or self._stop.is_set():
                return
            name = call["name"]
            try:
                args_obj = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
            except json.JSONDecodeError:
                args_obj = {}
            args_key = json.dumps(args_obj, sort_keys=True, ensure_ascii=False)[:240]

            result = harness.dispatch(str(name), args_obj if isinstance(args_obj, dict) else {})
            ok = bool(result.get("ok"))
            stuck = self._stuck.observe(str(name), args_key, ok=ok)
            model_text = tool_result_for_model(result)
            if stuck.get("stuck"):
                model_text = json.dumps(
                    {**json.loads(model_text), "stuck": stuck},
                    ensure_ascii=False,
                )
                self.emit(
                    {
                        "type": "agent_event",
                        "level": "error" if stuck.get("action") == "stop" else "info",
                        "message": stuck.get("message", "stuck"),
                    }
                )
                if stuck.get("action") == "stop":
                    self._actions_frozen = True

            bulky = "image_base64" in str(result) or name in {"screenshot", "click", "type_text", "hotkey", "open_app"}
            self._context.add(str(name), model_text, bulky=bulky)

            connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": model_text,
                }
            )

            image = extract_verify_image(result)
            if image and image.get("image_base64") and not self._actions_frozen:
                # Attach a compact screenshot so the model can see the UI (Realtime image input).
                try:
                    connection.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": (
                                        f"Verify screenshot after {name} "
                                        f"({image.get('width')}x{image.get('height')}). "
                                        f"{image.get('stub') or ''}"
                                    ),
                                },
                                {
                                    "type": "input_image",
                                    "image_url": f"data:image/png;base64,{image['image_base64']}",
                                },
                            ],
                        }
                    )
                except Exception as error:  # noqa: BLE001
                    self.emit(
                        {
                            "type": "agent_event",
                            "level": "error",
                            "message": f"screenshot attach failed: {error}",
                        }
                    )

            if name == "update_plan" or stuck.get("stuck"):
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
        # Do not emit realtime_interrupt here — that chopped live speech mid-sentence.
        # Tools stay frozen via _paused / _awaiting_continue; chat audio keeps playing.
        try:
            self.send_text(
                "SYSTEM NOTICE: The operator just moved the mouse themselves. "
                "Pause only desktop tools (clicks/typing). Keep talking normally. "
                "Briefly acknowledge you paused actions until they say continue or give a new task. "
                "Do not call desktop tools until they resume.",
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
