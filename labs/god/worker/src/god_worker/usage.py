"""Token / cost accounting for God sessions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _rate(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class UsageMeter:
    """Rough USD projection — override with GOD_INPUT_USD_PER_MTK / GOD_OUTPUT_USD_PER_MTK."""

    input_tokens: int = 0
    output_tokens: int = 0
    whisper_seconds: float = 0.0
    input_usd_per_mtok: float = field(default_factory=lambda: _rate("GOD_INPUT_USD_PER_MTK", 2.50))
    output_usd_per_mtok: float = field(default_factory=lambda: _rate("GOD_OUTPUT_USD_PER_MTK", 10.0))
    whisper_usd_per_min: float = field(default_factory=lambda: _rate("GOD_WHISPER_USD_PER_MIN", 0.006))

    def add_response_usage(self, usage: dict | None) -> dict:
        usage = usage or {}
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        self.input_tokens += inp
        self.output_tokens += out
        return self.snapshot(turn_input=inp, turn_output=out)

    def add_whisper_bytes(self, nbytes: int, sample_rate: int = 16_000) -> dict:
        # 16-bit mono WAV ≈ 2 bytes/sample
        seconds = max(0.0, nbytes / (sample_rate * 2))
        self.whisper_seconds += seconds
        return self.snapshot(whisper_seconds=seconds)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return (
            (self.input_tokens / 1_000_000) * self.input_usd_per_mtok
            + (self.output_tokens / 1_000_000) * self.output_usd_per_mtok
            + (self.whisper_seconds / 60.0) * self.whisper_usd_per_min
        )

    def snapshot(self, **extra) -> dict:
        payload = {
            "type": "usage",
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "whisper_seconds": round(self.whisper_seconds, 2),
            "rates": {
                "input_usd_per_mtok": self.input_usd_per_mtok,
                "output_usd_per_mtok": self.output_usd_per_mtok,
                "whisper_usd_per_min": self.whisper_usd_per_min,
            },
        }
        payload.update(extra)
        return payload
