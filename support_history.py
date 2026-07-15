"""Auditable current-chat storage for escalation; separate from semantic memory."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from guardrails import block_reason


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SupportHistory:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, topic TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS session_evidence(
            conversation_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            completed_at TEXT NOT NULL, handoff_summary TEXT NOT NULL, memory_status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_outbox(
            id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL UNIQUE,
            user_id TEXT NOT NULL, workspace_id TEXT NOT NULL, candidate TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error_message TEXT
        );
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

    def handoff(self, conversation_id: int, user_id: str, recalled_memory: list[str] | None = None) -> str:
        topics = [row[0] for row in self.db.execute("SELECT DISTINCT topic FROM issues WHERE conversation_id=? AND status='open'", (conversation_id,))]
        transcript = self.messages(conversation_id)
        customer_messages = [content for role, content, _ in transcript if role == "user"]
        bot_messages = [content for role, content, _ in transcript if role == "assistant"]
        latest_customer = customer_messages[-1] if customer_messages else "No customer request captured."
        facts = " | ".join(customer_messages[-3:]) or "No facts captured."
        bot_attempt = bot_messages[-1] if bot_messages else "No bot response captured."
        rendered = "\n".join(f"[{time}] {role.upper()}: {content}" for role, content, time in transcript)
        return ("HUMAN HANDOFF SUMMARY\n"
                f"Customer: {user_id}\nUnresolved: {', '.join(topics) or 'Needs review'}\n"
                f"Customer goal / latest need: {latest_customer}\n"
                f"Facts supplied by customer: {facts}\n"
                f"What the bot last tried: {bot_attempt}\n"
                f"Relevant prior memory: {' | '.join(recalled_memory or []) or 'None retrieved'}\n"
                "Escalation reason: The request needs human review or the customer asked for an agent.\n\nFULL TRANSCRIPT\n" + rendered)

    def memory_candidate(self, conversation_id: int, user_id: str, workspace_id: str) -> str:
        """Small, safe, structured record for long-term memory; never include full transcript."""
        transcript = self.messages(conversation_id)
        safe_customer_messages = [content for role, content, _ in transcript if role == "user" and not block_reason(content)]
        topics = [row[0] for row in self.db.execute("SELECT DISTINCT topic FROM issues WHERE conversation_id=? AND status='open'", (conversation_id,))]
        latest = safe_customer_messages[-1] if safe_customer_messages else "No safe customer fact captured."
        facts = " | ".join(safe_customer_messages[-2:]) or "No safe facts captured."
        return ("SUPPORT MEMORY\n"
                f"Workspace: {workspace_id}\nCustomer: {user_id}\n"
                f"Topic: {', '.join(topics) or 'general support'}\n"
                "Status: needs human review\n"
                f"Latest need: {latest}\n"
                f"Safe facts: {facts}\n"
                "Next action: review the current case with a human agent.")[:900]

    def enqueue_memory(self, conversation_id: int, user_id: str, workspace_id: str, candidate: str) -> int:
        now = _now()
        self.db.execute("""
            INSERT INTO memory_outbox(conversation_id,user_id,workspace_id,candidate,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET candidate=excluded.candidate,
              status=CASE WHEN memory_outbox.status='complete' THEN 'complete' ELSE 'pending' END,
              updated_at=excluded.updated_at, error_message=NULL
        """, (conversation_id, user_id, workspace_id, candidate, "pending", now, now))
        self.db.commit()
        return int(self.db.execute("SELECT id FROM memory_outbox WHERE conversation_id=?", (conversation_id,)).fetchone()[0])

    def claim_memory_job(self, job_id: int) -> tuple[int, int, str, str, str] | None:
        now = _now()
        cursor = self.db.execute("""UPDATE memory_outbox SET status='processing', attempts=attempts+1, updated_at=?
            WHERE id=? AND status='pending'""", (now, job_id))
        if not cursor.rowcount:
            self.db.commit()
            return None
        self.db.commit()
        return self.db.execute("SELECT id,conversation_id,user_id,workspace_id,candidate FROM memory_outbox WHERE id=?", (job_id,)).fetchone()

    def finish_memory_job(self, job_id: int, conversation_id: int, success: bool, error_message: str | None = None) -> None:
        status = "complete" if success else "failed"
        message = "Saved to local Mem0 OSS asynchronously." if success else f"Mem0 job failed: {error_message or 'unknown error'}"
        self.db.execute("UPDATE memory_outbox SET status=?,updated_at=?,error_message=? WHERE id=?", (status, _now(), error_message, job_id))
        self.db.execute("UPDATE session_evidence SET memory_status=? WHERE conversation_id=?", (message, conversation_id))
        self.db.commit()

    def memory_context(self, user_id: str, workspace_id: str, limit: int = 3) -> list[str]:
        """Fast SQLite-backed continuity while slow vector memory is pending or unavailable."""
        rows = self.db.execute("""SELECT candidate FROM memory_outbox
            WHERE user_id=? AND workspace_id=? AND status IN ('pending','processing','complete')
            ORDER BY updated_at DESC LIMIT ?""", (user_id, workspace_id, limit)).fetchall()
        return [row[0] for row in rows]

    def memory_job_status(self, conversation_id: int) -> str | None:
        row = self.db.execute("SELECT status FROM memory_outbox WHERE conversation_id=?", (conversation_id,)).fetchone()
        return row[0] if row else None

    def save_session_evidence(self, conversation_id: int, user_id: str, workspace_id: str,
                              handoff_summary: str, memory_status: str) -> None:
        """Durable demo/audit record. Transcript remains in messages; this stores the end-state packet."""
        self.db.execute("""
            INSERT INTO session_evidence(conversation_id, user_id, workspace_id, completed_at, handoff_summary, memory_status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET completed_at=excluded.completed_at,
              handoff_summary=excluded.handoff_summary, memory_status=excluded.memory_status
        """, (conversation_id, user_id, workspace_id, _now(), handoff_summary, memory_status))
        self.db.commit()

    def recent_evidence(self, limit: int = 10) -> list[tuple[int, str, str, str, str]]:
        return self.db.execute("""
            SELECT conversation_id, workspace_id, user_id, completed_at, memory_status
            FROM session_evidence ORDER BY completed_at DESC LIMIT ?
        """, (limit,)).fetchall()


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
