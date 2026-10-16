"""Stdio JSON-lines desktop worker for God."""

from __future__ import annotations

import json
import sys
import threading

from god_worker.agent_loop import run_agent_loop
from god_worker.desktop import normalize_screenshots
from god_worker.env import load_repo_env
from god_worker.mouse import install_visible_mouse
from god_worker.permissions import check_desktop
from god_worker.protocol import handle_request, parse_line
from god_worker.release import release_inputs
from god_worker.transcribe import transcribe_audio_base64
from god_worker.usage import UsageMeter


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def build_handlers(pyautogui, state: dict):
    def ping(_request):
        width, height = pyautogui.size()
        return {"type": "pong", "detail": f"{width}x{height}"}

    def click_center(_request):
        width, height = pyautogui.size()
        x, y = width // 2, height // 2
        pyautogui.click(x, y)
        return {"type": "result", "detail": f"clicked {x},{y}"}

    def stop(_request):
        state["cancel"].set()
        release_inputs()
        return {"type": "result", "detail": "released"}

    def agent_stop(_request):
        state["cancel"].set()
        release_inputs()
        return {"type": "result", "detail": "agent stop requested"}

    def agent_run(request):
        if state["thread"] is not None and state["thread"].is_alive():
            raise RuntimeError("An agent run is already in progress. Stop it first.")
        goal = request.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise RuntimeError("agent_run requires a nonempty goal string.")
        policy = request.get("policy") if isinstance(request.get("policy"), str) else ""
        max_turns = int(request.get("max_turns") or 12)
        talk = request.get("talk", True) is not False
        run_id = request.get("id")
        state["cancel"].clear()

        def work() -> None:
            try:
                final = run_agent_loop(
                    goal=goal,
                    policy=policy,
                    pyautogui=pyautogui,
                    emit=emit,
                    cancel=state["cancel"],
                    max_turns=max_turns,
                    meter=state["meter"],
                    talk=talk,
                )
                emit(state["meter"].snapshot())
                emit({"type": "result", "id": run_id, "ok": True, "detail": final})
            except Exception as error:  # noqa: BLE001
                emit({"type": "error", "id": run_id, "ok": False, "error": str(error)})
            finally:
                state["thread"] = None

        thread = threading.Thread(target=work, name="god-agent", daemon=True)
        state["thread"] = thread
        thread.start()
        return {"type": "ack", "detail": "agent started"}

    def transcribe(request):
        audio = request.get("audio_base64")
        if not isinstance(audio, str) or not audio.strip():
            raise RuntimeError("transcribe requires audio_base64.")
        fmt = request.get("format") if isinstance(request.get("format"), str) else "wav"
        import base64

        raw = base64.b64decode(audio)
        emit(state["meter"].add_whisper_bytes(len(raw)))
        text = transcribe_audio_base64(audio, fmt=fmt)
        return {"type": "result", "detail": text}

    def usage(_request):
        return {"type": "result", "detail": "ok", **state["meter"].snapshot()}

    return {
        "ping": ping,
        "click_center": click_center,
        "stop": stop,
        "agent_run": agent_run,
        "agent_stop": agent_stop,
        "transcribe": transcribe,
        "usage": usage,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_repo_env()
    import pyautogui

    pyautogui.FAILSAFE = True
    normalize_screenshots(pyautogui)

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

    state = {"cancel": threading.Event(), "thread": None, "meter": UsageMeter()}
    install_visible_mouse(pyautogui, emit)
    handlers = build_handlers(pyautogui, state)
    emit({"type": "ready", "ok": True, "detail": "god_worker ready"})
    emit(state["meter"].snapshot())

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
