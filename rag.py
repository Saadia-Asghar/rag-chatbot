"""Dependency-light local RAG retrieval used for trusted support knowledge."""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass


KNOWLEDGE = [
    ("billing-policy", "For duplicate charges, collect the invoice reference, charge date, amount, and last four digits only. Never ask for a full card number."),
    ("account-policy", "For account access issues, ask for the exact error message. Never ask a customer to share a password or one-time code."),
    ("handoff-policy", "Escalate a case when the user requests a human, the bot lacks sufficient context, or a payment dispute needs manual investigation."),
]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _similarity(query: str, document: str) -> float:
    q, d = Counter(_tokens(query)), Counter(_tokens(document))
    if not q or not d:
        return 0.0
    dot = sum(q[token] * d[token] for token in q.keys() & d.keys())
    return dot / math.sqrt(sum(x * x for x in q.values()) * sum(x * x for x in d.values()))


@dataclass(frozen=True)
class RAGHit:
    source: str
    text: str
    score: float


class KnowledgeBase:
    """Shared support documents; deliberately separate from user memory."""
    def __init__(self, db_path: str) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks(source TEXT, content TEXT, UNIQUE(source, content))")
        self.db.executemany("INSERT OR IGNORE INTO knowledge_chunks(source, content) VALUES (?, ?)", KNOWLEDGE)
        self.db.commit()
    def add(self, source: str, text: str) -> int:
        clean = " ".join(text.split())
        if not source.strip() or not clean: return 0
        chunks = [clean[i:i+700] for i in range(0, len(clean), 700)]
        before = self.db.total_changes
        self.db.executemany("INSERT OR IGNORE INTO knowledge_chunks(source, content) VALUES (?, ?)", [(source, chunk) for chunk in chunks])
        self.db.commit(); return self.db.total_changes - before
    def search(self, question: str, top_k: int = 3) -> list[RAGHit]:
        rows = self.db.execute("SELECT source, content FROM knowledge_chunks").fetchall()
        hits = [RAGHit(source, text, _similarity(question, text)) for source, text in rows]
        return [hit for hit in sorted(hits, key=lambda row: row.score, reverse=True)[:top_k] if hit.score > 0]


def retrieve(question: str, top_k: int = 3, knowledge_base: KnowledgeBase | None = None) -> list[RAGHit]:
    if not question.strip() or top_k <= 0:
        return []
    if knowledge_base:
        return knowledge_base.search(question, top_k)
    hits = [RAGHit(source, text, _similarity(question, text)) for source, text in KNOWLEDGE]
    return [hit for hit in sorted(hits, key=lambda item: item.score, reverse=True)[:top_k] if hit.score > 0]
