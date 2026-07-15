"""Tenant-isolated hybrid RAG: local Ollama embeddings plus keyword fallback."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache


KNOWLEDGE = [
    ("billing-policy", "For duplicate charges, collect the invoice reference, charge date, amount, and last four digits only. Never ask for a full card number."),
    ("account-policy", "For account access issues, ask for the exact error message. Never ask a customer to share a password or one-time code."),
    ("handoff-policy", "Escalate a case when the user requests a human, the bot lacks sufficient context, or a payment dispute needs manual investigation."),
]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _keyword_score(query: str, document: str) -> float:
    q, d = Counter(_tokens(query)), Counter(_tokens(document))
    if not q or not d:
        return 0.0
    dot = sum(q[token] * d[token] for token in q.keys() & d.keys())
    return dot / math.sqrt(sum(x * x for x in q.values()) * sum(x * x for x in d.values()))


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed locally. Empty result keeps the app usable if Ollama is offline."""
    if not texts:
        return []


@lru_cache(maxsize=256)
def _cached_query_embedding(question: str) -> tuple[float, ...]:
    """Avoid repeated local embedding work for repeated support questions."""
    vectors = _embed([question])
    return tuple(vectors[0]) if len(vectors) == 1 else ()
    try:
        from ollama import Client
        response = Client(host=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).embed(
            model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"), input=texts
        )
        return [[float(value) for value in vector] for vector in response["embeddings"]]
    except Exception:
        return []


@dataclass(frozen=True)
class RAGHit:
    source: str
    text: str
    score: float


class KnowledgeBase:
    """Company documents only; each search is restricted to one tenant plus shared policy."""
    def __init__(self, db_path: str) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks(source TEXT, content TEXT, tenant_id TEXT NOT NULL DEFAULT 'shared', embedding_json TEXT, UNIQUE(source, content, tenant_id))")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(knowledge_chunks)")}
        if "tenant_id" not in columns:
            self.db.execute("ALTER TABLE knowledge_chunks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'shared'")
        if "embedding_json" not in columns:
            self.db.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding_json TEXT")
        self.db.executemany("INSERT OR IGNORE INTO knowledge_chunks(source, content, tenant_id) VALUES (?, ?, 'shared')", KNOWLEDGE)
        self.db.commit()
        self._backfill_missing_embeddings()

    def _backfill_missing_embeddings(self) -> None:
        rows = self.db.execute("SELECT rowid, content FROM knowledge_chunks WHERE embedding_json IS NULL").fetchall()
        vectors = _embed([content for _, content in rows])
        if len(vectors) != len(rows):
            return
        self.db.executemany("UPDATE knowledge_chunks SET embedding_json=? WHERE rowid=?", [(json.dumps(vector), rowid) for (rowid, _), vector in zip(rows, vectors)])
        self.db.commit()

    def add(self, source: str, text: str, tenant_id: str = "shared") -> int:
        clean = " ".join(text.split())
        if not source.strip() or not clean:
            return 0
        chunks = [clean[i:i + 700] for i in range(0, len(clean), 700)]
        vectors = _embed(chunks)
        values = [(source, chunk, tenant_id, json.dumps(vector) if len(vectors) == len(chunks) else None) for chunk, vector in zip(chunks, vectors)]
        if len(values) != len(chunks):
            values = [(source, chunk, tenant_id, None) for chunk in chunks]
        before = self.db.total_changes
        self.db.executemany("INSERT OR IGNORE INTO knowledge_chunks(source, content, tenant_id, embedding_json) VALUES (?, ?, ?, ?)", values)
        self.db.commit()
        return self.db.total_changes - before

    def search(self, question: str, top_k: int = 3, tenant_id: str = "shared") -> list[RAGHit]:
        if not question.strip() or top_k <= 0:
            return []
        self._backfill_missing_embeddings()
        rows = self.db.execute("SELECT source, content, embedding_json FROM knowledge_chunks WHERE tenant_id IN (?, 'shared')", (tenant_id,)).fetchall()
        keyword_hits = [RAGHit(source, content, _keyword_score(question, content)) for source, content, _ in rows]
        # Most operational questions have an exact policy term. Return those immediately;
        # local semantic embedding is reserved for ambiguous wording and is cached thereafter.
        if keyword_hits and max(hit.score for hit in keyword_hits) >= 0.05:
            return [hit for hit in sorted(keyword_hits, key=lambda hit: hit.score, reverse=True) if hit.score > 0][:top_k]
        query_vector = list(_cached_query_embedding(question))
        hits = []
        for source, content, raw_vector in rows:
            keyword = _keyword_score(question, content)
            try:
                semantic = _cosine(query_vector, json.loads(raw_vector)) if query_vector and raw_vector else 0.0
            except (TypeError, ValueError):
                semantic = 0.0
            score = (0.75 * semantic + 0.25 * keyword) if query_vector and raw_vector else keyword
            if score > 0:
                hits.append(RAGHit(source, content, score))
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]


def retrieve(question: str, top_k: int = 3, knowledge_base: KnowledgeBase | None = None, tenant_id: str = "shared") -> list[RAGHit]:
    if knowledge_base:
        return knowledge_base.search(question, top_k, tenant_id)
    # A dependency-free fallback retained for unit tests and local diagnostics.
    return [RAGHit(source, text, _keyword_score(question, text)) for source, text in KNOWLEDGE if _keyword_score(question, text) > 0][:top_k]
