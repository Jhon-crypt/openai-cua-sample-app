"""Structured GUI action space for God (scaled clicks + text/element grounding)."""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Any, Callable

from god_worker.agent_loop import build_code_tool_definitions, execute_code
from god_worker.desktop import open_app
from god_worker.grounding import ax_find_elements, map_image_to_screen, ocr_find_text
from god_worker.mouse import agent_mouse_section

Emit = Callable[[dict[str, Any]], None]


def build_gui_tool_definitions(*, include_exec_py: bool = True) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": "open_app",
            "description": "Launch or focus any macOS app by name (Safari, Slack, Notes, Finder, Chrome…). Never Spotlight.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Exact Application name, e.g. Google Chrome, Notes."}
                },
            },
        },
        {
            "type": "function",
            "name": "screenshot",
            "description": (
                "Capture the screen for the model. Returns img_w/img_h of THIS image and screen_w/screen_h. "
                "click(x,y) uses coordinates in this image's pixel space (harness scales to the real screen)."
            ),
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "click_text",
            "description": (
                "PREFERRED: click visible on-screen text via OCR (e.g. query='John Oladele'). "
                "Use for Chrome profile tiles, buttons labeled with text, etc."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        },
        {
            "type": "function",
            "name": "click_element",
            "description": (
                "PREFERRED for native Mac controls: click an Accessibility node by title/label "
                "(optional role like AXButton). Falls back poorly for some Chrome web UI — use click_text there."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "role": {"type": "string", "description": "Optional AX role substring, e.g. button."},
                },
            },
        },
        {
            "type": "function",
            "name": "click",
            "description": (
                "Click at (x,y) in the LATEST screenshot pixel space (img_w×img_h from screenshot/verify). "
                "Prefer click_text/click_element when the target has a label."
            ),
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
            "description": "Create or replace the persistent multi-step plan for the current goal.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["plan"],
                "properties": {"plan": {"type": "string"}},
            },
        },
    ]
    if include_exec_py:
        for tool in build_code_tool_definitions():
            tools.append({k: v for k, v in tool.items() if k != "strict"})
    return tools


def _image_to_png_b64(image: Any, max_width: int = 1024) -> tuple[Any, str, int, int]:
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
    return img, base64.b64encode(buf.getvalue()).decode("ascii"), w, h


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
        self._last_shot_image: Any | None = None

    def dispatch(self, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        if not isinstance(arguments, dict):
            arguments = {}
        handlers = {
            "open_app": self._open_app,
            "screenshot": self._screenshot,
            "click": self._click,
            "click_text": self._click_text,
            "click_element": self._click_element,
            "type_text": self._type_text,
            "hotkey": self._hotkey,
            "update_plan": self._update_plan,
            "exec_py": self._exec_py,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        # Own the whole tool call so chained move→click isn't mistaken for the operator.
        mouse_tools = {"click", "click_text", "click_element", "type_text", "hotkey", "exec_py"}
        try:
            if name in mouse_tools:
                with agent_mouse_section(settle_s=0.45):
                    return handler(arguments)
            return handler(arguments)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error), "tool": name}

    def _screen_size(self) -> tuple[int, int]:
        size = self.pyautogui.size()
        return int(size[0]), int(size[1])

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
        screen_w, screen_h = self._screen_size()
        # Logical size after normalize_screenshots should already match screen_w/h.
        shot_img, b64, w, h = _image_to_png_b64(image)
        scale_x = screen_w / float(w) if w else 1.0
        scale_y = screen_h / float(h) if h else 1.0
        meta = {
            "img_w": w,
            "img_h": h,
            "width": w,
            "height": h,
            "screen_w": screen_w,
            "screen_h": screen_h,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "image_base64": b64,
            "format": "png",
            "verify_of": verify_of,
            "stub": f"[screenshot img={w}x{h} screen={screen_w}x{screen_h}]",
        }
        self._last_screenshot_meta = meta
        self._last_shot_image = shot_img
        self.emit(
            {
                "type": "agent_event",
                "level": "info",
                "message": f"screenshot img {w}x{h} → screen {screen_w}x{screen_h}",
            }
        )
        return {
            "img_w": w,
            "img_h": h,
            "width": w,
            "height": h,
            "screen_w": screen_w,
            "screen_h": screen_h,
            "scale_x": round(scale_x, 4),
            "scale_y": round(scale_y, 4),
            "stub": meta["stub"],
            "image_base64": b64,
            "format": "png",
            "verify_of": verify_of,
            "coord_space": "screenshot_pixels",
        }

    def _require_shot_meta(self) -> dict[str, Any]:
        if not self._last_screenshot_meta:
            self._screenshot_core(verify_of="auto_before_click")
        assert self._last_screenshot_meta is not None
        return self._last_screenshot_meta

    def _click_screen(self, screen_x: int, screen_y: int, *, button: str = "left") -> None:
        self.pyautogui.click(screen_x, screen_y, button=button)

    def _click(self, args: dict[str, Any]) -> dict[str, Any]:
        x = int(args["x"])
        y = int(args["y"])
        button = str(args.get("button") or "left")
        meta = self._require_shot_meta()
        screen_x, screen_y = map_image_to_screen(
            x,
            y,
            img_w=int(meta["img_w"]),
            img_h=int(meta["img_h"]),
            screen_w=int(meta["screen_w"]),
            screen_h=int(meta["screen_h"]),
        )
        self.emit(
            {
                "type": "agent_event",
                "level": "pending",
                "message": f"click img({x},{y}) → screen({screen_x},{screen_y})",
            }
        )
        self._click_screen(screen_x, screen_y, button=button)
        time.sleep(0.15)
        verify = self._screenshot_core(verify_of=f"click:{screen_x},{screen_y}")
        return {
            "ok": True,
            "x": x,
            "y": y,
            "screen_x": screen_x,
            "screen_y": screen_y,
            "button": button,
            "mapped_from": "screenshot_pixels",
            "verify": verify,
        }

    def _click_text(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise RuntimeError("click_text requires query.")
        # Fresh full-res logical screenshot for OCR accuracy, then map via that image size.
        full = self.pyautogui.screenshot()
        screen_w, screen_h = self._screen_size()
        self.emit({"type": "agent_event", "level": "pending", "message": f"click_text({query!r})"})
        hits = ocr_find_text(full, query)
        if not hits:
            raise RuntimeError(f"No OCR match for {query!r}.")
        best = hits[0]
        # OCR coords are in full image space (== screen logical size after normalize).
        img_w, img_h = int(best["img_w"]), int(best["img_h"])
        screen_x, screen_y = map_image_to_screen(
            best["x"],
            best["y"],
            img_w=img_w,
            img_h=img_h,
            screen_w=screen_w,
            screen_h=screen_h,
        )
        self._click_screen(screen_x, screen_y)
        time.sleep(0.15)
        verify = self._screenshot_core(verify_of=f"click_text:{query}")
        return {
            "ok": True,
            "query": query,
            "matched": best["text"],
            "score": best["score"],
            "screen_x": screen_x,
            "screen_y": screen_y,
            "candidates": [{"text": h["text"], "score": h["score"]} for h in hits[:5]],
            "verify": verify,
        }

    def _click_element(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        role = args.get("role")
        role_s = str(role).strip() if isinstance(role, str) and role.strip() else None
        if not query:
            raise RuntimeError("click_element requires query.")
        self.emit(
            {
                "type": "agent_event",
                "level": "pending",
                "message": f"click_element({query!r}, role={role_s!r})",
            }
        )
        hits = ax_find_elements(query, role=role_s)
        if not hits:
            raise RuntimeError(
                f"No Accessibility match for {query!r}. "
                "Grant Accessibility to the God worker, or use click_text for Chrome/web UI."
            )
        best = hits[0]
        screen_x, screen_y = int(best["x"]), int(best["y"])
        self._click_screen(screen_x, screen_y)
        time.sleep(0.15)
        verify = self._screenshot_core(verify_of=f"click_element:{query}")
        return {
            "ok": True,
            "query": query,
            "matched": best["label"],
            "role": best["role"],
            "score": best["score"],
            "screen_x": screen_x,
            "screen_y": screen_y,
            "candidates": [{"label": h["label"], "role": h["role"], "score": h["score"]} for h in hits[:5]],
            "verify": verify,
        }

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
            k: verify.get(k)
            for k in (
                "img_w",
                "img_h",
                "width",
                "height",
                "screen_w",
                "screen_h",
                "scale_x",
                "scale_y",
                "stub",
                "verify_of",
                "coord_space",
            )
            if verify.get(k) is not None
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
            "width": result.get("width") or result.get("img_w"),
            "height": result.get("height") or result.get("img_h"),
            "stub": result.get("stub"),
        }
    verify = result.get("verify")
    if isinstance(verify, dict) and isinstance(verify.get("image_base64"), str):
        return {
            "image_base64": verify["image_base64"],
            "width": verify.get("width") or verify.get("img_w"),
            "height": verify.get("height") or verify.get("img_h"),
            "stub": verify.get("stub"),
            "verify_of": verify.get("verify_of"),
        }
    return None
