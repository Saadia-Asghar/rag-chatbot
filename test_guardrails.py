from guardrails import block_reason, should_store_memory

def test_sensitive_data_is_blocked_and_not_stored():
    assert block_reason("My password is secret")
    assert not should_store_memory("My password is secret")

def test_billing_issue_is_worth_remembering():
    assert should_store_memory("My invoice has a duplicate charge")
