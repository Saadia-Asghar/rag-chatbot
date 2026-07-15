from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from memory_service import LocalMem0, process_memory_job
from guardrails import block_reason
from kb_ingestion import IngestionError, pdf_text, webpage_text
from llm_service import fast_policy_answer, generate_answer
from rag import KnowledgeBase, retrieve
from support_history import SupportHistory, reply_for, topic_for

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
st.set_page_config(page_title="QuickTalk-style Mem0 OSS demo", layout="wide")
st.title("QuickTalk-style support demo — RAG + Mem0 OSS")
st.caption("Local only: Ollama + Mem0 OSS + Chroma + SQLite. No cloud key.")


@st.cache_resource
def memory_worker() -> ThreadPoolExecutor:
    """Single worker prevents multiple expensive local Mem0 jobs from competing for Ollama."""
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem0-outbox")

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
    if st.button("Load 3 public tenant demo sources"):
        demo_sources = [
            ("nayatel-demo", "https://nayatel.com/faqs"),
            ("shifa-demo", "https://www.shifa.com.pk/city/islamabad"),
            ("general-demo", "https://support.mozilla.org/en-US/kb/what-firefox-account"),
        ]
        loaded = []
        for demo_tenant, demo_url in demo_sources:
            try:
                loaded.append(f"{demo_tenant}: {knowledge_base.add(demo_url, webpage_text(demo_url), demo_tenant)} chunks")
            except IngestionError as error:
                loaded.append(f"{demo_tenant}: unavailable ({error})")
        st.success(" | ".join(loaded))
    st.caption("KB is filtered by workspace. Mem0 uses workspace:customer as its user scope.")

if not st.session_state.conversation:
    st.info("Enter a customer ID and start a support session.")
    st.stop()

conversation, user_id = st.session_state.conversation, st.session_state.user_id
tenant_id = st.session_state.tenant_id
scoped_user_id = st.session_state.scoped_user_id
st.info(f"Workspace: **{tenant_id}**  |  Department: **{st.session_state.department}**  |  Customer: **{user_id}**")
if st.session_state.get("welcomed") != conversation:
    past = history.memory_context(scoped_user_id, tenant_id)
    st.session_state.prior_memory = past
    welcome = "Welcome. How can I help today?"
    if past:
        welcome = "Welcome back. I have your previous support-case context. Are you following up, or is there something new I can help with?"
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
    fast_answer = fast_policy_answer(message) if not blocked else None
    hits = retrieve(message, knowledge_base=knowledge_base, tenant_id=tenant_id) if not blocked and not fast_answer else []
    recalled = st.session_state.get("prior_memory", []) if not blocked and not fast_answer else []
    st.session_state.last_rag_retrieval = ([(hit.source, round(hit.score, 3)) for hit in hits] if not fast_answer
                                            else [("Fast duplicate-charge workflow (no embedding/LLM call)", 1.0)])
    response = blocked or fast_answer or generate_answer(message, [hit.text for hit in hits], recalled)
    _, escalate = reply_for(message, [hit.text for hit in hits])
    history.add_message(conversation, "user", message)
    history.add_open_issue(conversation, topic_for(message))
    history.add_message(conversation, "assistant", response)
    if escalate:
        st.session_state.escalated = True
    st.rerun()

with st.expander("Last RAG retrieval (test evidence)"):
    st.caption("Hybrid score = 75% local embedding similarity + 25% keyword similarity. Results are workspace filtered.")
    retrieved = st.session_state.get("last_rag_retrieval", [])
    if retrieved:
        st.dataframe(retrieved, column_config={0: "Knowledge source", 1: "Hybrid score"}, hide_index=True, use_container_width=True)
    else:
        st.info("Ask a question to see the retrieved knowledge chunks.")

col1, col2 = st.columns(2)
if col1.button("Escalate to human agent", type="primary"):
    st.session_state.escalated = True
if col2.button("End session and prepare context"):
    st.session_state.escalated = True
if st.session_state.get("escalated"):
    handoff_memory = st.session_state.get("prior_memory", [])
    if not st.session_state.get("summary_saved"):
        packet = history.handoff(conversation, user_id, handoff_memory)
        candidate = history.memory_candidate(conversation, user_id, tenant_id)
        job_id = history.enqueue_memory(conversation, scoped_user_id, tenant_id, candidate)
        history.save_session_evidence(conversation, user_id, tenant_id, packet, "Queued for local Mem0; SQLite continuity is available immediately.")
        memory_worker().submit(process_memory_job, DATA, DATA / "support.sqlite3", job_id)
        st.session_state.handoff_packet = packet
        st.session_state.memory_job_id = job_id
        st.session_state.summary_saved = True
    packet = st.session_state.get("handoff_packet") or history.handoff(conversation, user_id, handoff_memory)
    job_status = history.memory_job_status(conversation) or "not queued"
    st.warning(f"Session-end handoff packet ready. SQLite context is immediate; local Mem0 job status: **{job_status}**.")
    bot_column, agent_column = st.columns(2)
    with bot_column:
        st.subheader("Bot outcome")
        st.write("The bot has stopped and transferred the case with the specific summary shown to the agent.")
        st.caption("Current chat and concise continuity memory are retained in SQLite immediately. Mem0 runs from a single background outbox worker.")
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
**SQLite continuity cache:** returning-customer case summaries are immediately available, scoped to the signed-in workspace and customer.  
**Mem0 OSS:** a single background outbox worker sends only a concise approved summary to local Mem0; `nomic-embed-text` stores vectors in local Chroma.  
**Answer LLM:** `llama3.2:1b` receives the user message plus RAG chunks and cached customer context, not a full historic transcript.  
**Handoff:** SQLite keeps the full transcript; the human receives the complete transcript and unresolved issue summary without waiting for Mem0.
""")
