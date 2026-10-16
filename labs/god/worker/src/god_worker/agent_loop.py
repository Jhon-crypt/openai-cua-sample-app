"""Responses API → exec_py loop (same harness shape as python-app, sync for the worker)."""

from __future__ import annotations

import base64
import io
import json
import os
import threading
from typing import Any, Callable

Emit = Callable[[dict[str, Any]], None]


def build_code_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "exec_py",
            "strict": True,
            "description": "Execute Python in the persistent PyAutoGUI desktop session.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python for the local macOS desktop. Globals persist between calls. "
                            "Available: pyautogui, time, log(value), display(PIL_image), open_app(name). "
                            "To launch or focus an app, call open_app('Notes') — do NOT use Spotlight typing. "
                            "Start with display(pyautogui.screenshot()). Screenshot coordinates match clicks. "
                            "Obey operator policy. Do not disable the fail-safe."
                        ),
                    }
                },
            },
        }
    ]


def classify_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("status") != "completed" or response.get("error"):
        error = response.get("error")
        message = error.get("message", "") if isinstance(error, dict) else ""
        raise RuntimeError(f"Responses API did not complete: {message}".strip())
    output = response.get("output")
    if not isinstance(output, list):
        raise RuntimeError("Responses API output is missing.")
    calls: list[dict[str, Any]] = []
    commentary: list[str] = []
    final: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            continue
        if item.get("type") == "function_call":
            if item.get("name") != "exec_py":
                raise RuntimeError(f"Unexpected tool: {item.get('name')}")
            args = json.loads(item["arguments"])
            code = args.get("code")
            if not isinstance(code, str) or not code.strip():
                raise RuntimeError("exec_py requires nonempty code.")
            calls.append({"call_id": item["call_id"], "code": code, "arguments": item["arguments"]})
            continue
        if item.get("type") == "message" and item.get("role") == "assistant":
            phase = item.get("phase")
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text = part["text"].strip()
                    if not text:
                        continue
                    if phase == "commentary":
                        commentary.append(text)
                    else:
                        final.append(text)
    progress = "\n\n".join(commentary)
    if calls:
        return {"kind": "calls", "calls": calls, "commentary": progress}
    if final:
        return {"kind": "final", "text": "\n\n".join(final), "commentary": progress}
    if commentary:
        return {"kind": "commentary", "commentary": progress}
    raise RuntimeError("No tool calls or final message from the model.")


def execute_code(code: str, namespace: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    def log(*values: Any) -> None:
        output.append({"type": "input_text", "text": " ".join(str(v) for v in values)})

    def display(value: Any) -> None:
        from PIL import Image

        if isinstance(value, Image.Image):
            buf = io.BytesIO()
            value.save(buf, format="PNG")
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            output.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded}",
                    "detail": "original",
                }
            )
            return
        log(value)

    local_ns = dict(namespace)
    local_ns["log"] = log
    local_ns["display"] = display
    exec(code, local_ns, local_ns)  # noqa: S102 - intentional model code execution
    namespace.update({k: v for k, v in local_ns.items() if k not in {"log", "display"}})
    return output or [{"type": "input_text", "text": "exec_py completed with no output."}]


def run_agent_loop(
    *,
    goal: str,
    policy: str,
    pyautogui: Any,
    emit: Emit,
    cancel: threading.Event,
    max_turns: int = 12,
    model: str | None = None,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    chosen_model = model or os.environ.get("CUA_DEFAULT_MODEL") or "gpt-6-astra"
    instructions = "\n".join(
        [
            "You are God, a personal macOS desktop operator.",
            "Use exec_py with PyAutoGUI to inspect and control the screen.",
            "To open apps, always use open_app('AppName') (e.g. open_app('Notes')). Never rely on Spotlight.",
            "After open_app, sleep briefly, screenshot, then type or click only if the target UI is visible.",
            "Obey the operator policy exactly when present.",
            "Prefer short action sequences, then screenshot to verify.",
            "When finished, reply with a brief final answer (no tool call).",
            f"Operator policy:\n{policy.strip() or '(none — follow the goal safely)'}",
        ]
    )

    import time as time_mod

    from god_worker.desktop import open_app

    namespace: dict[str, Any] = {"pyautogui": pyautogui, "time": time_mod, "open_app": open_app}
    previous_response_id: str | None = None
    next_input: Any = goal.strip()
    emit({"type": "agent_event", "level": "info", "message": f"Agent started ({chosen_model})."})

    for turn in range(1, max_turns + 1):
        if cancel.is_set():
            raise RuntimeError("Agent stopped by operator.")
        request: dict[str, Any] = {
            "instructions": instructions,
            "input": next_input,
            "model": chosen_model,
            "parallel_tool_calls": False,
            "reasoning": {"effort": "low"},
            "truncation": "auto",
            "tools": build_code_tool_definitions(),
        }
        if previous_response_id is not None:
            request["previous_response_id"] = previous_response_id

        emit({"type": "agent_event", "level": "pending", "message": f"Model turn {turn}…"})
        response_obj = client.responses.create(**request)
        response = response_obj.model_dump(exclude_none=True)
        previous_response_id = response["id"]
        classified = classify_response(response)

        if classified.get("commentary"):
            emit({"type": "agent_event", "level": "info", "message": classified["commentary"]})

        if classified["kind"] == "final":
            emit({"type": "agent_event", "level": "ok", "message": classified["text"]})
            return classified["text"]
        if classified["kind"] == "commentary":
            next_input = []
            continue

        tool_outputs = []
        for call in classified["calls"]:
            if cancel.is_set():
                raise RuntimeError("Agent stopped by operator.")
            emit(
                {
                    "type": "agent_event",
                    "level": "pending",
                    "message": f"exec_py: {call['code'][:180]}{'…' if len(call['code']) > 180 else ''}",
                }
            )
            try:
                output = execute_code(call["code"], namespace)
            except Exception as error:  # noqa: BLE001 - return to model
                output = [{"type": "input_text", "text": f"exec_py error: {error}"}]
                emit({"type": "agent_event", "level": "error", "message": str(error)})
            tool_outputs.append(
                {"type": "function_call_output", "call_id": call["call_id"], "output": output}
            )
        next_input = tool_outputs

    raise RuntimeError(f"Exhausted {max_turns} turns without a final answer.")
