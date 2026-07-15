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


def test_history_and_handoff_evidence_survive_reopen(tmp_path):
    path = tmp_path / "support.sqlite3"
    history = SupportHistory(path)
    conversation = history.start("nayatel-demo:demo-billing-001")
    history.add_message(conversation, "user", "My duplicate charge is still unresolved")
    history.add_message(conversation, "assistant", "Please share the invoice reference and amount.")
    packet = history.handoff(conversation, "demo-billing-001")
    history.save_session_evidence(conversation, "demo-billing-001", "nayatel-demo", packet, "Saved locally")

    reopened = SupportHistory(path)
    assert "duplicate charge" in reopened.messages(conversation)[0][1]
    evidence = reopened.recent_evidence()
    assert evidence[0][1:3] == ("nayatel-demo", "demo-billing-001")
