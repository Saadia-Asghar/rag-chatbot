"""Cheap local checks before invoking the LLM or writing a memory."""
import re


def block_reason(text: str) -> str | None:
    lower = text.lower()
    if any(phrase in lower for phrase in ("ignore previous instructions", "reveal system prompt", "show your prompt")):
        return "I can help with your support issue, but I cannot follow instructions that override the support workflow."
    if any(word in lower for word in ("password", "one-time code", "otp", "cvv")) or re.search(r"\b(?:\d[ -]?){13,16}\b", text):
        return "For your security, do not send passwords, one-time codes, CVV values, or full card numbers. Please share an invoice reference or the exact error instead."
    return None


def should_store_memory(text: str) -> bool:
    """Store decisions/preferences/cases, not greetings, secrets, or transient chatter."""
    if block_reason(text) or len(text.split()) < 4:
        return False
    durable_markers = ("bill", "charge", "invoice", "refund", "account", "preference", "follow up", "case", "issue")
    return any(marker in text.lower() for marker in durable_markers)
