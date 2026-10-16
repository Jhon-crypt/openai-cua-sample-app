from god_worker.agent_loop import classify_response


def test_classify_final_message():
    response = {
        "status": "completed",
        "id": "resp_1",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Done"}],
            }
        ],
    }
    result = classify_response(response)
    assert result["kind"] == "final"
    assert result["text"] == "Done"


def test_classify_tool_call():
    response = {
        "status": "completed",
        "id": "resp_2",
        "output": [
            {
                "type": "function_call",
                "name": "exec_py",
                "call_id": "call_1",
                "arguments": '{"code":"log(1)"}',
            }
        ],
    }
    result = classify_response(response)
    assert result["kind"] == "calls"
    assert result["calls"][0]["code"] == "log(1)"
