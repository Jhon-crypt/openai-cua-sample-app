"""Talk to GPT-Live without a microphone and report what comes back.

Speaks a sentence with macOS `say`, streams it (paced like a real mic) between
stretches of silence, then prints every non-audio event plus how much output
audio arrived and how much of it was audible. Costs a few cents per run.

    uv run --project labs/god/worker python labs/god/worker/scripts/live_probe.py
    uv run --project labs/god/worker python labs/god/worker/scripts/live_probe.py "Hey God, open Chrome"
"""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from websockets.sync.client import connect

from god_worker.env import load_repo_env
from god_worker.live_session import LIVE_URL, build_session_start

CHUNK_BYTES = 24 * 2 * 40  # 40 ms of pcm16 mono @ 24 kHz, same as the HUD
AUDIBLE_RMS = 328  # ≈ 0.01 full scale; matches SpeechPlayer.audibleRMS


def say_to_pcm(text: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp, "s.aiff"), Path(tmp, "s.wav")
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(aiff), str(wav)], check=True)
        data = wav.read_bytes()
    return data[data.find(b"data") + 8 :]


def main() -> int:
    load_repo_env()
    text = sys.argv[1] if len(sys.argv) > 1 else "Hey God, what time zone am I in?"
    speech = say_to_pcm(text)
    ws = connect(
        LIVE_URL,
        additional_headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}"},
        max_size=None,
    )
    ws.send(json.dumps(build_session_start("", model=os.environ.get("GOD_LIVE_MODEL", "gpt-live-1"), voice="marin")))
    t0 = time.time()
    stats = {"audio_ms": 0.0, "audible_ms": 0.0}

    def receive() -> None:
        for raw in ws:
            event = json.loads(raw)
            etype = event.get("type")
            if etype == "session.output_audio.delta":
                pcm = base64.b64decode(event["delta"])
                n = len(pcm) // 2
                samples = struct.unpack(f"<{n}h", pcm[: n * 2])
                rms = (sum(s * s for s in samples) / max(1, n)) ** 0.5
                stats["audio_ms"] += n / 24
                if rms >= AUDIBLE_RMS:
                    stats["audible_ms"] += n / 24
                continue
            if etype != "session.usage.updated":
                rest = {k: v for k, v in event.items() if k not in ("type", "delta", "event_id")}
                print(f"{time.time() - t0:5.1f}s {etype} {event.get('delta', '')!r} {json.dumps(rest)[:140]}")
            if etype == "session.closed":
                return

    threading.Thread(target=receive, daemon=True).start()

    def stream(buf: bytes) -> None:
        for i in range(0, len(buf), CHUNK_BYTES):
            chunk = buf[i : i + CHUNK_BYTES].ljust(CHUNK_BYTES, b"\0")
            ws.send(json.dumps({"type": "session.input_audio.append", "audio": base64.b64encode(chunk).decode()}))
            time.sleep(0.04)

    silence = bytes(CHUNK_BYTES)
    stream(silence * 100)  # 4 s
    stream(speech)
    stream(silence * 200)  # 8 s for the reply
    print(f"output audio {stats['audio_ms']:.0f} ms, audible {stats['audible_ms']:.0f} ms")
    ws.send(json.dumps({"type": "session.close"}))
    time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
