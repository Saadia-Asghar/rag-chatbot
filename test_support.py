from support_history import SupportHistory, topic_for
from rag import retrieve


def test_handoff_contains_full_current_transcript(tmp_path):
    history = SupportHistory(tmp_path / "support.sqlite3")
    conversation = history.start("alice")
    history.add_message(conversation, "user", "I was charged twice")
    history.add_open_issue(conversation, "billing or payment")
    handoff = history.handoff(conversation, "alice")
    assert "billing or payment" in handoff
    assert "I was charged twice" in handoff


def test_rag_retrieves_billing_policy():
    assert any(hit.source == "billing-policy" for hit in retrieve("I have a duplicate charge"))


def test_topic_detection():
    assert topic_for("My invoice is wrong") == "billing or payment"
