"""Unit tests for Realtime session helpers (no network)."""

from god_worker.realtime_session import (
    build_instructions,
    build_realtime_tools,
    build_turn_detection,
    is_noise_like_transcript,
    normalize_noise_reduction,
    session_config,
    should_resume_from_transcript,
)


def test_instructions_mention_desktop_handoff():
    text = build_instructions("Do not delete files.", plan="running: open Notes")
    assert "run_desktop_task" in text
    assert "BACKGROUND" in text or "background" in text.lower()
    assert "Do not delete files." in text
    assert "ANY macOS application" in text
    assert "announce" in text.lower() or "Briefly tell" in text
    assert "running: open Notes" in text
    assert "background noise" in text.lower()
    # Inline GUI tools must not be the Realtime path anymore.
    assert "click_text" not in text
    assert "Do NOT invent meaning" in text or "do not invent meaning" in text.lower()


def test_realtime_tools_are_voice_control_only():
    tools = build_realtime_tools()
    names = {t["name"] for t in tools}
    assert names == {"run_desktop_task", "cancel_desktop_task", "get_desktop_status"}
    assert all("strict" not in t for t in tools)


def test_session_config_far_field_uses_server_vad():
    cfg = session_config("be careful", "gpt-realtime-2.1", "marin", plan="todo")
    assert cfg["type"] == "realtime"
    assert cfg["model"] == "gpt-realtime-2.1"
    assert cfg["audio"]["input"]["format"]["rate"] == 24000
    assert cfg["audio"]["input"]["noise_reduction"]["type"] == "far_field"
    turn = cfg["audio"]["input"]["turn_detection"]
    assert turn["type"] == "server_vad"
    assert turn["threshold"] == 0.58
    assert turn["create_response"] is False
    assert turn["interrupt_response"] is False
    assert cfg["audio"]["input"]["transcription"]["language"] == "en"
    assert cfg["audio"]["output"]["voice"] == "marin"
    assert "todo" in cfg["instructions"]
    assert {t["name"] for t in cfg["tools"]} == {
        "run_desktop_task",
        "cancel_desktop_task",
        "get_desktop_status",
    }


def test_session_config_near_field_uses_semantic_vad():
    cfg = session_config("x", "gpt-realtime-2.1", "marin", noise_reduction="near_field")
    assert cfg["audio"]["input"]["noise_reduction"]["type"] == "near_field"
    turn = cfg["audio"]["input"]["turn_detection"]
    assert turn["type"] == "semantic_vad"
    assert turn["eagerness"] == "low"
    assert turn["create_response"] is False
    assert turn["interrupt_response"] is True


def test_build_turn_detection_pairs_with_nr():
    far = build_turn_detection("far_field")
    assert far["type"] == "server_vad"
    assert 0.5 <= far["threshold"] <= 0.95
    near = build_turn_detection("near_field")
    assert near["type"] == "semantic_vad"


def test_normalize_noise_reduction():
    assert normalize_noise_reduction("near_field") == "near_field"
    assert normalize_noise_reduction("NEAR-FIELD") == "near_field"
    assert normalize_noise_reduction("far_field") == "far_field"
    assert normalize_noise_reduction(None) == "far_field"
    assert normalize_noise_reduction("") == "far_field"


def test_should_resume_rejects_noise_asr():
    assert is_noise_like_transcript("這是")
    assert is_noise_like_transcript("Squid.")
    assert not should_resume_from_transcript("I was Miami.")
    assert not should_resume_from_transcript("Squid.")
    assert not should_resume_from_transcript("這是")
    assert not should_resume_from_transcript("uh huh mm")
    assert not should_resume_from_transcript("the")


def test_should_resume_accepts_clear_intent():
    assert should_resume_from_transcript("continue")
    assert should_resume_from_transcript("yes")
    assert should_resume_from_transcript("go ahead")
    assert should_resume_from_transcript("can you open Notes please")
