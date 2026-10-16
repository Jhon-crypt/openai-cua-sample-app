"""OpenAI audio transcription for God voice input."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path


def transcribe_audio_base64(audio_base64: str, fmt: str = "wav") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")

    raw = base64.b64decode(audio_base64)
    if len(raw) < 2_000:
        raise RuntimeError("Recording too short or empty. Hold Mic for at least one second, then Stop Mic.")

    from openai import OpenAI

    suffix = f".{fmt.lstrip('.')}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(raw)
        path = Path(handle.name)
    try:
        client = OpenAI(api_key=api_key)
        with path.open("rb") as audio:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="en",
            )
        text = getattr(result, "text", None) or str(result)
        text = text.strip()
        if not text:
            raise RuntimeError("Whisper returned empty text. Try speaking more clearly for 2–3 seconds.")
        return text
    finally:
        path.unlink(missing_ok=True)
