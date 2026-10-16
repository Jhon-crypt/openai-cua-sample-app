"""Stdio JSON-lines desktop worker for God."""

from __future__ import annotations

import json
import sys

from god_worker.permissions import check_desktop
from god_worker.protocol import handle_request, parse_line
from god_worker.release import release_inputs


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def build_handlers(pyautogui):
    def ping(_request):
        width, height = pyautogui.size()
        return {"type": "pong", "detail": f"{width}x{height}"}

    def click_center(_request):
        width, height = pyautogui.size()
        x, y = width // 2, height // 2
        pyautogui.click(x, y)
        return {"type": "result", "detail": f"clicked {x},{y}"}

    def stop(_request):
        release_inputs()
        return {"type": "result", "detail": "released"}

    return {"ping": ping, "click_center": click_center, "stop": stop}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    import pyautogui

    pyautogui.FAILSAFE = True

    if "--check" in argv:
        try:
            width, height = check_desktop(pyautogui)
            emit({"type": "ok", "ok": True, "detail": f"desktop {width}x{height}"})
            return 0
        except Exception as error:  # noqa: BLE001
            emit({"type": "error", "ok": False, "error": str(error)})
            return 1

    try:
        check_desktop(pyautogui)
    except Exception as error:  # noqa: BLE001
        emit({"type": "error", "ok": False, "error": str(error)})
        return 1

    handlers = build_handlers(pyautogui)
    emit({"type": "ready", "ok": True, "detail": "god_worker ready"})

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = parse_line(line)
        except Exception as error:  # noqa: BLE001
            emit({"type": "error", "ok": False, "error": str(error)})
            continue
        emit(handle_request(request, handlers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
