"""Structured GUI action space for God (paper-style predefined tools + exec_py escape)."""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Any, Callable

from god_worker.agent_loop import build_code_tool_definitions, execute_code
from god_worker.desktop import open_app

Emit = Callable[[dict[str, Any]], None]


def build_gui_tool_definitions(*, include_exec_py: bool = True) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": "open_app",
            "description": "Launch or focus any macOS app by name (Safari, Slack, Notes, Finder, …). Never use Spotlight.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Exact Application name, e.g. Notes, Safari, Slack."}
                },
            },
        },
        {
            "type": "function",
            "name": "screenshot",
            "description": "Capture the screen. Coordinates match click(x,y). Prefer after open_app or before clicking.",
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "click",
            "description": "Move the visible cursor slowly to (x,y) then click. Screen coords match screenshot.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["x", "y"],
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right"], "default": "left"},
                },
            },
        },
        {
            "type": "function",
            "name": "type_text",
            "description": "Type text into the focused UI (UTF-8). Use after clicking a field.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        },
        {
            "type": "function",
            "name": "hotkey",
            "description": "Press a key combo, e.g. keys=['command','c'] or ['command','tab'].",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["keys"],
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
            },
        },
        {
            "type": "function",
            "name": "update_plan",
            "description": "Create or replace the persistent multi-step plan for the current goal. Call before long UI tasks.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["plan"],
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": "Numbered steps and status (todo/doing/done).",
                    }
                },
            },
        },
    ]
    if include_exec_py:
        for tool in build_code_tool_definitions():
            tools.append({k: v for k, v in tool.items() if k != "strict"})
    return tools


def _image_to_png_b64(image: Any, max_width: int = 1024) -> tuple[str, int, int]:
    from PIL import Image

    if not isinstance(image, Image.Image):
        raise TypeError("screenshot must be a PIL Image")
    img = image.convert("RGB")
    w, h = img.size
    if w > max_width:
        ratio = max_width / float(w)
        img = img.resize((max_width, max(1, int(h * ratio))), Image.Resampling.LANCZOS)
        w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), w, h


class DesktopHarness:
    """Execute structured GUI tools against a shared PyAutoGUI session."""

    def __init__(self, pyautogui: Any, emit: Emit | None = None) -> None:
        import time as time_mod

        self.pyautogui = pyautogui
        self.emit = emit or (lambda _payload: None)
        self.plan = ""
        self._namespace: dict[str, Any] = {
            "pyautogui": pyautogui,
            "time": time_mod,
            "open_app": open_app,
        }
        self._last_screenshot_meta: dict[str, Any] | None = None

    def dispatch(self, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        if not isinstance(arguments, dict):
            arguments = {}
        handlers = {
            "open_app": self._open_app,
            "screenshot": self._screenshot,
            "click": self._click,
            "type_text": self._type_text,
            "hotkey": self._hotkey,
            "update_plan": self._update_plan,
            "exec_py": self._exec_py,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        try:
            return handler(arguments)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error), "tool": name}

    def _open_app(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "").strip()
        if not name:
            raise RuntimeError("open_app requires name.")
        self.emit({"type": "agent_event", "level": "pending", "message": f"open_app({name!r})"})
        detail = open_app(name)
        time.sleep(0.6)
        verify = self._screenshot_core(verify_of=f"open_app:{name}")
        return {"ok": True, "detail": detail, "verify": verify}

    def _screenshot(self, _args: dict[str, Any]) -> dict[str, Any]:
        self.emit({"type": "agent_event", "level": "pending", "message": "screenshot()"})
        return {"ok": True, **self._screenshot_core()}

    def _screenshot_core(self, verify_of: str | None = None) -> dict[str, Any]:
        image = self.pyautogui.screenshot()
        b64, w, h = _image_to_png_b64(image)
        meta = {
            "width": w,
            "height": h,
            "image_base64": b64,
            "format": "png",
            "bytes": len(b64) * 3 // 4,
            "verify_of": verify_of,
            "stub": f"[screenshot {w}x{h} png]",
        }
        self._last_screenshot_meta = meta
        # HUD can ignore; keep payload light in events
        self.emit({"type": "agent_event", "level": "info", "message": f"screenshot {w}x{h}"})
        return {
            "width": w,
            "height": h,
            "stub": meta["stub"],
            "image_base64": b64,
            "format": "png",
            "verify_of": verify_of,
        }

    def _click(self, args: dict[str, Any]) -> dict[str, Any]:
        x = int(args["x"])
        y = int(args["y"])
        button = str(args.get("button") or "left")
        self.emit({"type": "agent_event", "level": "pending", "message": f"click({x},{y})"})
        self.pyautogui.click(x, y, button=button)
        time.sleep(0.15)
        verify = self._screenshot_core(verify_of=f"click:{x},{y}")
        return {"ok": True, "x": x, "y": y, "button": button, "verify": verify}

    def _type_text(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            raise RuntimeError("type_text requires text.")
        self.emit(
            {
                "type": "agent_event",
                "level": "pending",
                "message": f"type_text({text[:80]!r}{'…' if len(text) > 80 else ''})",
            }
        )
        self.pyautogui.write(text, interval=0.02)
        time.sleep(0.1)
        verify = self._screenshot_core(verify_of="type_text")
        return {"ok": True, "chars": len(text), "verify": verify}

    def _hotkey(self, args: dict[str, Any]) -> dict[str, Any]:
        keys = args.get("keys")
        if not isinstance(keys, list) or not keys:
            raise RuntimeError("hotkey requires nonempty keys array.")
        key_list = [str(k) for k in keys]
        self.emit({"type": "agent_event", "level": "pending", "message": f"hotkey({key_list})"})
        self.pyautogui.hotkey(*key_list)
        time.sleep(0.2)
        verify = self._screenshot_core(verify_of=f"hotkey:{'+'.join(key_list)}")
        return {"ok": True, "keys": key_list, "verify": verify}

    def _update_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        plan = str(args.get("plan") or "").strip()
        if not plan:
            raise RuntimeError("update_plan requires plan.")
        self.plan = plan
        self.emit({"type": "agent_event", "level": "info", "message": f"plan updated ({len(plan)} chars)"})
        return {"ok": True, "plan": plan}

    def _exec_py(self, args: dict[str, Any]) -> dict[str, Any]:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            raise RuntimeError("exec_py requires nonempty code.")
        self.emit(
            {
                "type": "agent_event",
                "level": "pending",
                "message": f"exec_py: {code[:180]}{'…' if len(code) > 180 else ''}",
            }
        )
        parts = execute_code(code, self._namespace)
        text_bits: list[str] = []
        for part in parts:
            if part.get("type") == "input_text":
                text_bits.append(str(part.get("text", "")))
            elif part.get("type") == "input_image":
                text_bits.append("[screenshot from exec_py]")
        return {"ok": True, "output": "\n".join(text_bits) or "exec_py completed."}


def tool_result_for_model(result: dict[str, Any]) -> str:
    """Compact JSON string for Realtime function_call_output (no huge base64)."""
    slim = dict(result)
    verify = slim.get("verify")
    if isinstance(verify, dict):
        slim["verify"] = {
            "width": verify.get("width"),
            "height": verify.get("height"),
            "stub": verify.get("stub"),
            "verify_of": verify.get("verify_of"),
        }
    if "image_base64" in slim:
        slim["image_base64"] = "<omitted; see attached screenshot item if present>"
        slim["has_image"] = True
    return json.dumps(slim, ensure_ascii=False)


def extract_verify_image(result: dict[str, Any]) -> dict[str, Any] | None:
    """Pull PNG base64 from a tool result or nested verify block."""
    if isinstance(result.get("image_base64"), str) and result.get("image_base64"):
        return {
            "image_base64": result["image_base64"],
            "width": result.get("width"),
            "height": result.get("height"),
            "stub": result.get("stub"),
        }
    verify = result.get("verify")
    if isinstance(verify, dict) and isinstance(verify.get("image_base64"), str):
        return {
            "image_base64": verify["image_base64"],
            "width": verify.get("width"),
            "height": verify.get("height"),
            "stub": verify.get("stub"),
            "verify_of": verify.get("verify_of"),
        }
    return None
