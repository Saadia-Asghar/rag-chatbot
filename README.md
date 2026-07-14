# Local RAG Support Bot + Mem0 Open Source

This project uses **only [Mem0 Open Source](https://github.com/mem0ai/mem0)**. It installs the official `mem0ai` package and calls its self-hosted `Memory.from_config(...)` API. It does not use `MemoryClient`, `MEM0_API_KEY`, or the Mem0 hosted platform.

## Architecture

```text
Customer message
  ├─ Local RAG: retrieve support-policy chunks
  ├─ Mem0 OSS: search local user-scoped memories
  │    └─ Ollama LLM extracts facts; Ollama embeddings create vectors; Chroma persists vectors
  ├─ Bot response
  ├─ Mem0 OSS: save useful turn under user_id
  └─ SQLite: retain complete current transcript

Escalation → unresolved-issue summary + complete SQLite transcript → human agent
```

Mem0 is semantic long-term memory, not the authoritative chat record. The human handoff always uses the full transcript so no important detail is missed.

## Prerequisites — no API keys

1. Install [Ollama](https://ollama.com/).
2. In a terminal, pull the local models:

```powershell
ollama pull llama3.2:1b
ollama pull nomic-embed-text
```

3. Install Python dependencies and start the app:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Open `http://localhost:8501`. All values are local by default:

- `llama3.2:1b`: lightweight local LLM that extracts durable memory facts for Mem0.
- `nomic-embed-text`: embedding model that turns memories and queries into vectors.
- Chroma: local vector store at `data/chroma/`.
- SQLite: local support transcript at `data/support.sqlite3`.

Optional environment variables: `OLLAMA_BASE_URL`, `OLLAMA_CHAT_MODEL`, and `OLLAMA_EMBEDDING_MODEL`.

## Demo flow

1. Start as `alice` and type: `I was charged twice for my bill.`
2. Start another session as `alice`. The bot uses her local Mem0 history to give a return-user welcome.
3. Type `I need a human agent.` Click **Escalate to human agent** and show the packet.
4. Start as `bob`; Alice's memory must not appear because retrieval is filtered by `user_id`.

## Tests

```powershell
py -3 -m pytest -q
```

## Hybrid RAG embeddings (local, free)

QuickTalk uses Pinecone in its cloud architecture, so this demo also tests semantic retrieval without a paid cloud service. When a PDF, pasted document, or public website page is indexed, local `nomic-embed-text` creates one vector for each approximately 700-character chunk and stores it beside that chunk in SQLite. For a customer question, the app embeds the question once and combines semantic cosine similarity (75%) with keyword similarity (25%).

This makes “my payment appeared twice” match a “duplicate charge” policy even when exact words differ. Retrieval is always limited to the selected workspace plus explicitly shared policies; Nayatel documents cannot be returned to a Shifa customer. If local Ollama is unavailable, the app falls back to keyword-only retrieval so human handoff still works.

`nomic-embed-text` is used for both the knowledge base and Mem0. Mem0 additionally stores customer-scoped long-term-memory vectors in local Chroma. The complete conversation remains in SQLite.

## Production mapping to a Pinecone/GCP architecture

| Demo component | Production equivalent |
|---|---|
| SQLite knowledge vectors | Pinecone namespace/index filtered by `tenant_id` |
| Ollama `nomic-embed-text` | Approved production embedding model/endpoint |
| Local Ollama `llama3.2:1b` | Approved GCP-hosted answer LLM |
| SQLite transcripts | Managed GCP case/audit database |
| Local Chroma Mem0 storage | Approved persistent user-memory vector store |

Keep three stores separate: RAG contains organization knowledge, Mem0 contains short customer-scoped durable facts, and the case database contains the full transcript. Never put raw chats from every customer into one shared, unfiltered vector namespace.

## Cost-aware workflow

- Indexing/changing a document: embed each new chunk once.
- Each customer question: embed the question once, retrieve at most three chunks, then make one answer-LLM call.
- New session: retrieve only the signed-in customer’s top memories.
- End session/escalation: save one approved Mem0 memory summary, not one memory per message.

For high volume, cache retrieved session memories and repeat the memory search only when the customer refers to history or changes topic.
