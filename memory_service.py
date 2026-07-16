"""Mem0 OSS adapter only: local Ollama models + local Chroma vector store.

This module intentionally does not import MemoryClient and never reads a
MEM0_API_KEY. It uses Mem0's open-source Memory API directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class LocalMem0:
    def __init__(self, data_dir: str | Path) -> None:
        self.memory: Any | None = None
        self.status = "Local Mem0 has not started."
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        chat_model = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:1b")
        embedding_model = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
        config = {
            "llm": {"provider": "ollama", "config": {"model": chat_model, "ollama_base_url": base_url}},
            "embedder": {"provider": "ollama", "config": {"model": embedding_model, "ollama_base_url": base_url}},
            "vector_store": {
                "provider": "chroma",
                "config": {"collection_name": "support_memories", "path": str(Path(data_dir) / "chroma")},
            },
        }
        try:
            from mem0 import Memory
            self.memory = Memory.from_config(config)
            self.status = f"Mem0 OSS active: Ollama {chat_model} + {embedding_model}; local Chroma storage."
        except Exception as error:
            self.status = f"Mem0 OSS unavailable: {error}"

    @property
    def available(self) -> bool:
        return self.memory is not None

    def recall(self, user_id: str, query: str) -> list[str]:
        if not self.memory or not user_id.strip() or not query.strip():
            return []
        response = self.memory.search(query, filters={"user_id": user_id}, top_k=3)
        return [str(row["memory"]) for row in response.get("results", []) if row.get("memory")]

    def remember_session(self, user_id: str, handoff_summary: str, metadata: dict[str, Any] | None = None) -> None:
        """Persist a pre-approved structured record without another LLM extraction pass."""
        if self.memory and user_id.strip():
            self.memory.add(
                [{"role": "user", "content": handoff_summary}],
                user_id=user_id,
                metadata=metadata or {},
                infer=False,
            )


def process_memory_job(data_dir: str | Path, history_path: str | Path, job_id: int) -> None:
    """Process one durable outbox item without blocking customer or agent UI."""
    from support_history import SupportHistory

    history = SupportHistory(history_path)
    job = history.claim_memory_job(job_id)
    if not job:
        return
    _, conversation_id, user_id, workspace_id, candidate = job
    try:
        memory = LocalMem0(data_dir)
        if not memory.available:
            raise RuntimeError(memory.status)
        memory.remember_session(user_id, candidate, metadata={
            "workspace_id": workspace_id,
            "memory_type": "support_case",
            "review_required": True,
        })
        history.finish_memory_job(job_id, conversation_id, True)
    except Exception as error:
        history.finish_memory_job(job_id, conversation_id, False, str(error)[:300])
