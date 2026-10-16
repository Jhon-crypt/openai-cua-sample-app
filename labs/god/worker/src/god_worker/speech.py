"""Text-to-speech for agent talk-back."""

from __future__ import annotations

import base64
import os
from typing import Any, Callable

Emit = Callable[[dict[str, Any]], None]


def speak(client: Any, text: str, emit: Emit, voice: str | None = None) -> None:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return
    clipped = cleaned[:400]
    chosen = voice or os.environ.get("GOD_TTS_VOICE", "alloy")
    model = os.environ.get("GOD_TTS_MODEL", "tts-1")
    try:
        response = client.audio.speech.create(
            model=model,
            voice=chosen,
            input=clipped,
        )
        raw = getattr(response, "content", None)
        if raw is None and hasattr(response, "read"):
            raw = response.read()
        if raw is None:
            raw = bytes(response)
        emit(
            {
                "type": "speak",
                "text": clipped,
                "format": "mp3",
                "audio_base64": base64.b64encode(raw).decode("ascii"),
            }
        )
    except Exception as error:  # noqa: BLE001 - talk-back must not kill the agent loop
        emit({"type": "agent_event", "level": "error", "message": f"TTS failed: {error}"})
