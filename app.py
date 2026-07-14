from pathlib import Path

import streamlit as st

from memory_service import LocalMem0
from guardrails import block_reason
from kb_ingestion import IngestionError, pdf_text, webpage_text
from llm_service import generate_answer
from rag import KnowledgeBase, retrieve
from support_history import SupportHistory, reply_for, topic_for

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
st.set_page_config(page_title="QuickTalk-style Mem0 OSS demo", layout="wide")
st.title("QuickTalk-style support demo — RAG + Mem0 OSS")
st.caption("Local only: Ollama + Mem0 OSS + Chroma + SQLite. No cloud key.")

if "history" not in st.session_state:
    st.session_state.history = SupportHistory(DATA / "support.sqlite3")
if "knowledge_base" not in st.session_state:
    st.session_state.knowledge_base = KnowledgeBase(str(DATA / "knowledge.sqlite3"))
if "mem0" not in st.session_state:
    st.session_state.mem0 = LocalMem0(DATA)
if "conversation" not in st.session_state:
    st.session_state.conversation = None

history, memory, knowledge_base = st.session_state.history, st.session_state.mem0, st.session_state.knowledge_base
with st.sidebar:
    st.header("Workspace setup")
    tenant_id = st.selectbox("Client workspace", ["nayatel-demo", "shifa-demo", "general-demo"])
    department = st.selectbox("Department", ["Billing", "Account support", "General support"])
    st.caption(memory.status)
    user_id = st.text_input("Customer ID (demo sign-in)", st.session_state.get("user_id", "customer-001"))
    st.caption("Demo only: production must take this ID from real authentication.")
    if st.button("Sign in and start support", type="primary") and user_id.strip():
        st.session_state.user_id = user_id.strip()
        st.session_state.tenant_id = tenant_id
        st.session_state.department = department
        st.session_state.scoped_user_id = f"{tenant_id}:{user_id.strip()}"
        st.session_state.conversation = history.start(st.session_state.scoped_user_id)
        st.session_state.escalated = False
        st.session_state.summary_saved = False
        st.rerun()
    st.divider(); st.subheader("Knowledge base setup")
    kb_source = st.text_input("Document name", "support-policy")
    kb_text = st.text_area("Paste support policy / FAQ text")
    if st.button("Index knowledge document"):
        st.success(f"Indexed {knowledge_base.add(kb_source, kb_text, tenant_id)} new chunks for {tenant_id}.")
    uploaded_pdf = st.file_uploader("Upload PDF knowledge document", type=["pdf"])
    if st.button("Index uploaded PDF"):
        try:
            if uploaded_pdf is None:
                raise IngestionError("Choose a PDF first.")
            added = knowledge_base.add(uploaded_pdf.name, pdf_text(uploaded_pdf.getvalue()), tenant_id)
            st.success(f"Indexed {added} PDF chunks for {tenant_id}.")
        except IngestionError as error:
            st.error(str(error))
    kb_url = st.text_input("Public website link", placeholder="https://example.com/support")
    if st.button("Index website link"):
        try:
            added = knowledge_base.add(kb_url, webpage_text(kb_url), tenant_id)
            st.success(f"Indexed {added} website chunks for {tenant_id}.")
        except IngestionError as error:
            st.error(str(error))
    st.caption("KB is filtered by workspace. Mem0 uses workspace:customer as its user scope.")

if not st.session_state.conversation:
    st.info("Enter a customer ID and start a support session.")
    st.stop()

conversation, user_id = st.session_state.conversation, st.session_state.user_id
tenant_id = st.session_state.tenant_id
scoped_user_id = st.session_state.scoped_user_id
st.info(f"Workspace: **{tenant_id}**  |  Department: **{st.session_state.department}**  |  Customer: **{user_id}**")
if st.session_state.get("welcomed") != conversation:
    past = memory.recall(scoped_user_id, "open issue previous support follow up") if memory.available else []
    welcome = "Welcome. How can I help today?"
    if past:
        welcome = "Welcome back. Are you following up on a previous support issue, or is there something new I can help with?"
    history.add_message(conversation, "assistant", welcome)
    st.session_state.welcomed = conversation

for role, content, _ in history.messages(conversation):
    display_role = role if role in {"user", "assistant"} else "assistant"
    avatar = "👤" if role == "agent" else None
    with st.chat_message(display_role, avatar=avatar):
        if role == "agent":
            st.caption("Human agent")
        st.write(content)

message = st.chat_input("Example: I was charged twice for my bill")
if message:
    blocked = block_reason(message)
    hits = retrieve(message, knowledge_base=knowledge_base, tenant_id=tenant_id) if not blocked else []
    recalled = memory.recall(scoped_user_id, message) if memory.available and not blocked else []
    response = blocked or generate_answer(message, [hit.text for hit in hits], recalled)
    _, escalate = reply_for(message, [hit.text for hit in hits])
    history.add_message(conversation, "user", message)
    history.add_open_issue(conversation, topic_for(message))
    history.add_message(conversation, "assistant", response)
    if escalate:
        st.session_state.escalated = True
    st.rerun()

col1, col2 = st.columns(2)
if col1.button("Escalate to human agent", type="primary"):
    st.session_state.escalated = True
if col2.button("End session and prepare context"):
    st.session_state.escalated = True
if st.session_state.get("escalated"):
    handoff_memory = memory.recall(scoped_user_id, "previous support issue and unresolved customer need") if memory.available else []
    packet = history.handoff(conversation, user_id, handoff_memory)
    memory_status = "Not available; session packet retained in SQLite only."
    if memory.available and not st.session_state.get("summary_saved"):
        memory.remember_session(scoped_user_id, packet)
        st.session_state.summary_saved = True
    if memory.available:
        memory_status = "Saved to local Mem0 OSS (session-end summary only)."
    history.save_session_evidence(conversation, user_id, tenant_id, packet, memory_status)
    st.warning("Session-end handoff packet ready. Mem0 is written once here, not per chat message.")
    bot_column, agent_column = st.columns(2)
    with bot_column:
        st.subheader("Bot outcome")
        st.write("The bot has stopped and transferred the case with the specific summary shown to the agent.")
        st.caption("Current chat is retained in SQLite; the approved session summary is also stored in local Mem0.")
    with agent_column:
        st.subheader("Human Agent Inbox")
        st.caption("This is the exact context delivered at escalation.")
        st.text_area("Specific handoff summary", packet, height=330, key="agent_packet")
        agent_reply = st.text_input("Human agent reply to customer", key="agent_reply")
        if st.button("Send human-agent reply", key="send_agent_reply") and agent_reply.strip():
            history.add_message(conversation, "agent", agent_reply.strip())
            st.success("Human-agent reply added to the case transcript.")
            st.rerun()

with st.expander("Persistent demo evidence (SQLite)"):
    st.caption("Each completed session retains its full transcript in messages and its final handoff packet in session_evidence.")
    evidence = history.recent_evidence()
    if evidence:
        st.dataframe(evidence, column_config={
            0: "Conversation", 1: "Workspace", 2: "Customer", 3: "Completed at", 4: "Memory result"
        }, hide_index=True, use_container_width=True)
    else:
        st.info("End or escalate one session to create evidence for your comparison.")

with st.expander("Architecture used in this demo"):
    st.markdown("""
**RAG:** the support policy chunks are ranked for each customer message.  
**Mem0 OSS:** the local Ollama LLM extracts durable facts once at session end; `nomic-embed-text` embeds the summary; Chroma stores the vectors locally.  
**Answer LLM:** `llama3.2:1b` receives the user message plus RAG chunks and only the signed-in user's recalled Mem0 memories.  
**Returning customer:** `Memory.search(... filters={workspace:customer})` retrieves only that workspace's customer's previous memories.  
**Handoff:** SQLite keeps the whole current transcript; the human receives the complete transcript and unresolved issue summary.
""")
