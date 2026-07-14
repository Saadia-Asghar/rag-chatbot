"""Auditable current-chat storage for escalation; separate from semantic memory."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SupportHistory:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, topic TEXT NOT NULL, status TEXT NOT NULL);
        """)
        self.db.commit()

    def start(self, user_id: str) -> int:
        cursor = self.db.execute("INSERT INTO conversations(user_id, created_at) VALUES (?, ?)", (user_id, _now()))
        self.db.commit()
        return int(cursor.lastrowid)

    def add_message(self, conversation_id: int, role: str, content: str) -> None:
        clean = " ".join(content.split())
        if clean:
            self.db.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (conversation_id, role, clean, _now()))
            self.db.commit()

    def add_open_issue(self, conversation_id: int, topic: str) -> None:
        exists = self.db.execute("SELECT 1 FROM issues WHERE conversation_id=? AND topic=? AND status='open'", (conversation_id, topic)).fetchone()
        if not exists:
            self.db.execute("INSERT INTO issues(conversation_id, topic, status) VALUES (?, ?, 'open')", (conversation_id, topic))
            self.db.commit()

    def messages(self, conversation_id: int) -> list[tuple[str, str, str]]:
        return self.db.execute("SELECT role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()

    def handoff(self, conversation_id: int, user_id: str) -> str:
        topics = [row[0] for row in self.db.execute("SELECT DISTINCT topic FROM issues WHERE conversation_id=? AND status='open'", (conversation_id,))]
        transcript = self.messages(conversation_id)
        latest_customer = next((content for role, content, _ in reversed(transcript) if role == "user"), "No customer request captured.")
        rendered = "\n".join(f"[{time}] {role.upper()}: {content}" for role, content, time in transcript)
        return ("HUMAN HANDOFF SUMMARY\n"
                f"Customer: {user_id}\nUnresolved: {', '.join(topics) or 'Needs review'}\n"
                f"Latest customer need: {latest_customer}\n"
                "Bot outcome: Escalated; human review required.\n\nFULL TRANSCRIPT\n" + rendered)


def topic_for(message: str) -> str:
    words = message.lower()
    if any(token in words for token in ("bill", "charge", "refund", "invoice", "payment")):
        return "billing or payment"
    if any(token in words for token in ("login", "password", "sign in", "account")):
        return "account access"
    return "general support"


def reply_for(message: str, rag_context: list[str]) -> tuple[str, bool]:
    if any(token in message.lower() for token in ("human", "agent", "representative", "escalate")):
        return "I am escalating this to a human agent with the full conversation context.", True
    if topic_for(message) == "billing or payment":
        return "I can help investigate the billing issue. Please provide the invoice reference, date, and amount—never your full card number.", False
    if topic_for(message) == "account access":
        return "Please share the exact sign-in error message. Do not send your password or one-time code.", False
    return "I can help. Please share the relevant reference number or error message.", False
