import base64

import pytest

from god_worker.transcribe import transcribe_audio_base64


def test_rejects_tiny_audio(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="too short"):
        transcribe_audio_base64(base64.b64encode(b"abc").decode(), fmt="wav")
