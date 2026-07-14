from pathlib import Path

import streamlit as st

from memory_service import LocalMem0
from rag import retrieve
from support_history import SupportHistory, reply_for, topic_for

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
st.set_page_config(page_title="Local Mem0 OSS Support RAG", layout="wide")
st.title("Local RAG support bot with Mem0 Open Source")
st.caption("No Mem0 Cloud API. Ollama + Mem0 OSS + local Chroma + local SQLite.")

if "history" not in st.session_state:
    st.session_state.history = SupportHistory(DATA / "support.sqlite3")
if "mem0" not in st.session_state:
    st.session_state.mem0 = LocalMem0(DATA)
if "conversation" not in st.session_state:
    st.session_state.conversation = None

history, memory = st.session_state.history, st.session_state.mem0
with st.sidebar:
    st.header("Local setup")
    st.caption(memory.status)
    user_id = st.text_input("Authenticated customer ID", st.session_state.get("user_id", "customer-001"))
    if st.button("Start support session", type="primary") and user_id.strip():
        st.session_state.user_id = user_id.strip()
        st.session_state.conversation = history.start(user_id.strip())
        st.session_state.escalated = False
        st.rerun()
    st.markdown("Run locally: `ollama pull llama3.1:8b` and `ollama pull nomic-embed-text`.")

if not st.session_state.conversation:
    st.info("Enter a customer ID and start a support session.")
    st.stop()

conversation, user_id = st.session_state.conversation, st.session_state.user_id
if st.session_state.get("welcomed") != conversation:
    past = memory.recall(user_id, "previous support issue",) if memory.available else []
    welcome = "Welcome. How can I help today?"
    if past:
        welcome = "Welcome back. Are you following up on a previous support issue, or is there something new I can help with?"
    history.add_message(conversation, "assistant", welcome)
    st.session_state.welcomed = conversation

for role, content, _ in history.messages(conversation):
    with st.chat_message(role):
        st.write(content)

message = st.chat_input("Example: I was charged twice for my bill")
if message:
    hits = retrieve(message)
    response, escalate = reply_for(message, [hit.text for hit in hits])
    history.add_message(conversation, "user", message)
    history.add_open_issue(conversation, topic_for(message))
    history.add_message(conversation, "assistant", response)
    if memory.available:
        memory.remember_turn(user_id, message, response)
    if escalate:
        st.session_state.escalated = True
    st.rerun()

if st.button("Escalate to human agent", type="primary"):
    st.session_state.escalated = True
if st.session_state.get("escalated"):
    st.warning("Handoff packet ready. It includes the full transcript, not only retrieved memory.")
    st.text_area("Human-agent context", history.handoff(conversation, user_id), height=340)

with st.expander("Architecture used in this demo"):
    st.markdown("""
**RAG:** the support policy chunks are ranked for each customer message.  
**Mem0 OSS:** the local Ollama LLM extracts durable facts; `nomic-embed-text` embeds memory and queries; Chroma stores the vectors locally.  
**Returning customer:** `Memory.search(... filters={user_id})` retrieves only that customer's previous memories.  
**Handoff:** SQLite keeps the whole current transcript; the human receives the complete transcript and unresolved issue summary.
""")
