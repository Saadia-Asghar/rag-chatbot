"""Local Ollama answer generation grounded in RAG and Mem0 retrieval."""

from __future__ import annotations

import os


def build_messages(question: str, rag_context: list[str], memories: list[str]) -> list[dict[str, str]]:
    sources = "\n".join(f"- {item}" for item in rag_context) or "- No matching policy chunk found."
    recalled = "\n".join(f"- {item}" for item in memories) or "- No relevant previous memory found."
    system = f"""You are a careful customer-support assistant.
Use the support-policy context and user memory below. Do not invent account, payment, or policy facts.
Never ask for passwords, one-time codes, or full card numbers. If you cannot resolve the issue, offer a human handoff.

SUPPORT-POLICY RAG CONTEXT:
{sources}

USER-SCOPED MEM0 MEMORY:
{recalled}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def generate_answer(question: str, rag_context: list[str], memories: list[str]) -> str:
    """Use a local model only; return a usable fallback if Ollama is unavailable."""
    try:
        from ollama import Client
        response = Client(host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).chat(
            model=os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:1b"),
            messages=build_messages(question, rag_context, memories),
            options={"temperature": 0.2, "num_predict": 180},
        )
        return response["message"]["content"].strip()
    except Exception:
        return "I could not generate a local-model answer. I can still escalate this with the full transcript for a human agent."
