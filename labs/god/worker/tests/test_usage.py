import pytest

from god_worker.usage import UsageMeter


def test_usage_accumulates_and_projects_cost():
    meter = UsageMeter(input_usd_per_mtok=1.0, output_usd_per_mtok=2.0, whisper_usd_per_min=0.6)
    snap = meter.add_response_usage({"input_tokens": 1_000_000, "output_tokens": 500_000})
    assert snap["input_tokens"] == 1_000_000
    assert snap["output_tokens"] == 500_000
    assert snap["cost_usd"] == pytest.approx(2.0)
    meter.add_whisper_bytes(16_000 * 2 * 60)  # ~60s of 16kHz 16-bit mono
    assert meter.cost_usd == pytest.approx(2.6)
