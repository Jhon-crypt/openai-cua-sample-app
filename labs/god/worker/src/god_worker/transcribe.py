"""OpenAI audio transcription for God voice input."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path


def transcribe_audio_base64(audio_base64: str, fmt: str = "m4a") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Set it in the repo .env file.")

    from openai import OpenAI

    raw = base64.b64decode(audio_base64)
    suffix = f".{fmt.lstrip('.')}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(raw)
        path = Path(handle.name)
    try:
        client = OpenAI(api_key=api_key)
        with path.open("rb") as audio:
            result = client.audio.transcriptions.create(model="whisper-1", file=audio)
        text = getattr(result, "text", None) or str(result)
        return text.strip()
    finally:
        path.unlink(missing_ok=True)
