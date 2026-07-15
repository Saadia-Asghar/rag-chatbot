"""Local Ollama answer generation grounded in RAG and Mem0 retrieval."""

from __future__ import annotations

import os


def fast_policy_answer(question: str) -> str | None:
    """Fast, deterministic answers for high-frequency, safety-sensitive support intents."""
    lower = question.lower()
    duplicate_charge = (
        "duplicate charge" in lower
        or "charged twice" in lower
        or "payment appeared twice" in lower
        or "double charge" in lower
    )
    if duplicate_charge:
        return (
            "I can help investigate a possible duplicate charge. Please share the invoice reference, "
            "charge date, and amount; do not send your password, OTP, CVV, or full card number. "
            "If the charge needs manual review, I can transfer the case to a human agent."
        )
    return None


def build_messages(question: str, rag_context: list[str], memories: list[str]) -> list[dict[str, str]]:
    sources = "\n".join(f"- {item[:900]}" for item in rag_context[:2]) or "- No matching policy chunk found."
    recalled = "\n".join(f"- {item[:600]}" for item in memories[:2]) or "- No relevant previous memory found."
    system = f"""You are a careful customer-support assistant.
Use the support-policy context and user memory below. Do not invent account, payment, or policy facts.
Never ask for passwords, one-time codes, CVVs, or full card numbers. Reply in at most three short sentences. If policy context is insufficient, offer a human handoff.

SUPPORT-POLICY RAG CONTEXT:
{sources}

USER-SCOPED MEM0 MEMORY:
{recalled}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def generate_answer(question: str, rag_context: list[str], memories: list[str]) -> str:
    """Use a local model only; return a usable fallback if Ollama is unavailable."""
    fast_answer = fast_policy_answer(question)
    if fast_answer:
        return fast_answer
    if not rag_context:
        return "I do not have matching approved support-policy context for that request. I can transfer this case to a human agent with the full transcript."
    try:
        from ollama import Client
        response = Client(host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).chat(
            model=os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:1b"),
            messages=build_messages(question, rag_context, memories),
            options={"temperature": 0.1, "num_predict": 80, "num_ctx": 2048},
            keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "20m"),
        )
        return response["message"]["content"].strip()
    except Exception:
        return "I could not generate a local-model answer. I can still escalate this with the full transcript for a human agent."
