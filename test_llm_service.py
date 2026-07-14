from llm_service import build_messages


def test_prompt_contains_rag_and_user_memory():
    messages = build_messages("What happened?", ["Policy says collect invoice."], ["User had a duplicate charge."])
    prompt = messages[0]["content"]
    assert "Policy says collect invoice." in prompt
    assert "User had a duplicate charge." in prompt
    assert messages[1]["content"] == "What happened?"
