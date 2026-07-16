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


def test_memory_outbox_is_tenant_scoped_and_excludes_secrets(tmp_path):
    history = SupportHistory(tmp_path / "support.sqlite3")
    conversation = history.start("nayatel-demo:alice")
    history.add_message(conversation, "user", "My invoice INV-42 has a duplicate charge")
    history.add_message(conversation, "user", "My password is do-not-store-this")
    history.add_open_issue(conversation, "billing or payment")
    candidate = history.memory_candidate(conversation, "alice", "nayatel-demo")
    assert "INV-42" in candidate
    assert "do-not-store-this" not in candidate
    job = history.enqueue_memory(conversation, "nayatel-demo:alice", "nayatel-demo", candidate)
    assert history.memory_context("nayatel-demo:alice", "nayatel-demo") == [candidate]
    assert history.memory_context("shifa-demo:alice", "shifa-demo") == []
    claimed = history.claim_memory_job(job)
    assert claimed and claimed[0] == job
    history.finish_memory_job(job, conversation, True)
    assert history.memory_job_status(conversation) == "complete"


def test_small_talk_and_secrets_do_not_create_long_term_memory(tmp_path):
    history = SupportHistory(tmp_path / "support.sqlite3")
    conversation = history.start("nayatel-demo:alice")
    history.add_message(conversation, "user", "Hello there")
    history.add_message(conversation, "user", "My password is do-not-store-this")
    assert history.memory_candidate(conversation, "alice", "nayatel-demo") is None


def test_mem0_write_is_exact_and_skips_llm_inference():
    class FakeMemory:
        def __init__(self):
            self.calls = []

        def add(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"results": [{"id": "mem-1", "event": "ADD"}]}

    from memory_service import LocalMem0
    adapter = LocalMem0.__new__(LocalMem0)
    adapter.memory = fake = FakeMemory()
    memory_id = adapter.remember_session("nayatel-demo:alice", "SUPPORT MEMORY", {"workspace_id": "nayatel-demo"})
    _, kwargs = fake.calls[0]
    assert kwargs["infer"] is False
    assert kwargs["metadata"]["workspace_id"] == "nayatel-demo"
    assert memory_id == "mem-1"


def test_agent_correction_requeues_an_update_instead_of_another_case(tmp_path):
    history = SupportHistory(tmp_path / "support.sqlite3")
    conversation = history.start("nayatel-demo:alice")
    history.add_message(conversation, "user", "My invoice INV-42 has a duplicate charge")
    candidate = history.memory_candidate(conversation, "alice", "nayatel-demo")
    assert candidate
    job = history.enqueue_memory(conversation, "nayatel-demo:alice", "nayatel-demo", candidate)
    history.claim_memory_job(job)
    history.finish_memory_job(job, conversation, True, mem0_memory_id="mem-42")

    feedback_job = history.apply_agent_feedback(conversation, "nayatel-demo:alice", "nayatel-demo", "corrected", "Bank reversal, not a duplicate charge.")
    assert feedback_job == job
    claimed = history.claim_memory_job(job)
    assert claimed and claimed[5:7] == ("update", "mem-42")
    assert "Bank reversal" in claimed[4]
