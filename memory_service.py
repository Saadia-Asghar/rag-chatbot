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
        vector_store = os.getenv("MEM0_VECTOR_STORE", "chroma").lower()
        if vector_store in {"qdrant", "qdrant-server"}:
            vector_config = {"provider": "qdrant", "config": {
                "collection_name": "support_memories", "host": os.getenv("QDRANT_HOST", "localhost"),
                "port": int(os.getenv("QDRANT_PORT", "6333")), "embedding_model_dims": 768,
                "on_disk": True,
            }}
        elif vector_store in {"qdrant-local", "qdrant_local"}:
            vector_store = "qdrant-local"
            vector_config = {"provider": "qdrant", "config": {
                "collection_name": "support_memories", "path": str(Path(data_dir) / "qdrant"),
                "embedding_model_dims": 768, "on_disk": True,
            }}
        else:
            vector_store = "chroma"
            vector_config = {"provider": "chroma", "config": {
                "collection_name": "support_memories", "path": str(Path(data_dir) / "chroma"),
            }}
        config = {
            "llm": {"provider": "ollama", "config": {"model": chat_model, "ollama_base_url": base_url}},
            "embedder": {"provider": "ollama", "config": {"model": embedding_model, "ollama_base_url": base_url}},
            "vector_store": vector_config,
        }
        try:
            from mem0 import Memory
            self.memory = Memory.from_config(config)
            self.status = f"Mem0 OSS active: Ollama {chat_model} + {embedding_model}; local {vector_store} storage."
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

    def remember_session(self, user_id: str, handoff_summary: str, metadata: dict[str, Any] | None = None) -> str | None:
        """Persist a pre-approved structured record without another LLM extraction pass."""
        if self.memory and user_id.strip():
            response = self.memory.add(
                [{"role": "user", "content": handoff_summary}],
                user_id=user_id,
                metadata=metadata or {},
                infer=False,
            )
            results = response.get("results", []) if isinstance(response, dict) else []
            return next((str(item.get("id")) for item in results if item.get("id")), None)
        return None

    def update_memory(self, memory_id: str, summary: str, metadata: dict[str, Any]) -> None:
        if not self.memory:
            raise RuntimeError("Mem0 OSS is unavailable")
        self.memory.update(memory_id=memory_id, data=summary, metadata=metadata)


def process_memory_job(data_dir: str | Path, history_path: str | Path, job_id: int) -> None:
    """Process one durable outbox item without blocking customer or agent UI."""
    from support_history import SupportHistory

    history = SupportHistory(history_path)
    job = history.claim_memory_job(job_id)
    if not job:
        return
    _, conversation_id, user_id, workspace_id, candidate, action, memory_id = job
    try:
        memory = LocalMem0(data_dir)
        if not memory.available:
            raise RuntimeError(memory.status)
        metadata = {
            "workspace_id": workspace_id,
            "memory_type": "support_case",
            "review_required": True,
        }
        if action == "update" and memory_id:
            memory.update_memory(memory_id, candidate, metadata)
            stored_id = memory_id
        else:
            stored_id = memory.remember_session(user_id, candidate, metadata)
        history.finish_memory_job(job_id, conversation_id, True, mem0_memory_id=stored_id)
    except Exception as error:
        history.finish_memory_job(job_id, conversation_id, False, str(error)[:300])
