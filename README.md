# Local RAG Support Bot + Mem0 Open Source

This project uses **only [Mem0 Open Source](https://github.com/mem0ai/mem0)**. It installs the official `mem0ai` package and calls its self-hosted `Memory.from_config(...)` API. It does not use `MemoryClient`, `MEM0_API_KEY`, or the Mem0 hosted platform.

**New to RAG, agents, and memory?** Start with [LEARNING_GUIDE.md](LEARNING_GUIDE.md). It explains every component, shows the demo flow, and includes free learning/certificate links.

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

### Local performance behaviour

On a CPU-only laptop, local embeddings can take several seconds for a new semantic query. The demo therefore uses a keyword-first cascade: clear policy-term matches return immediately; only weak/ambiguous wording uses a cached semantic embedding lookup. Duplicate-charge questions use a deterministic safe workflow and skip both the embedding and answer-LLM call. Open-ended answers are limited to two context chunks and 80 generated tokens, and Ollama keeps the answer model warm for 20 minutes.

## Resilient memory flow

The full transcript is never sent to Mem0. At handoff, SQLite immediately saves the transcript, agent packet, and a short redacted `SUPPORT MEMORY` candidate. A single background outbox worker later submits that concise candidate to Mem0 OSS. Returning users receive the SQLite continuity cache immediately, scoped by workspace and customer; the vector-memory job is supplementary and visible as `pending`, `processing`, `complete`, or `failed`.

The candidate is already created and safety-filtered by application code, so the worker calls Mem0 with `infer=False`. This avoids a second local LLM extraction pass; Mem0 stores the approved candidate exactly and creates its vector representation. Each stored record includes workspace, record type, and review-required metadata.

RAG responses now include `Sources used:` whenever knowledge chunks were retrieved, so users and agents can inspect the grounding source instead of treating model text as an authority.

This prevents slow local Mem0/Ollama work from delaying the customer or human agent. Production should process the same durable outbox through a separate worker/queue and enforce timeouts, retries, monitoring, retention, and human correction/deletion of long-term memories.

## Human-agent memory lifecycle

The human-agent inbox has an explicit **Case outcome** decision:

- `resolved`: updates the case memory to show the case is closed.
- `unresolved`: keeps a follow-up requirement in the customer context.
- `corrected`: replaces an incorrect earlier case fact with the agent's correction.

Each decision is stored in the `agent_feedback` audit table. When Mem0 returned a memory ID for the original record, the background worker calls the OSS `memory.update(memory_id, data, metadata)` operation on that exact vector record. It does **not** add a contradictory second memory. If no Mem0 ID exists yet, the outbox safely adds the first approved record. SQLite remains authoritative during an OSS outage, and records every correction for review.

## Free local vector-store choices

The default is **Chroma**, an embedded free/open-source vector store at `data/chroma/`; no extra service is needed. **Embedded Qdrant OSS** is also available without Docker because `qdrant-client` is installed locally:

```powershell
$env:MEM0_VECTOR_STORE = "qdrant-local"
streamlit run app.py --server.port 8503
```

It persists to `data/qdrant/`. For a closer production-style separate-service demonstration, this repository also includes Qdrant Docker configuration:

```powershell
docker compose -f docker-compose.qdrant.yml up -d
$env:MEM0_VECTOR_STORE = "qdrant"
streamlit run app.py --server.port 8503
```

The Docker Qdrant service persists at `data/qdrant/` and serves locally on port 6333. Chroma is preferable for the simplest laptop demo; embedded Qdrant proves the same vector-store engine without Docker; server Qdrant is preferable for demonstrating a separately managed vector service. Neither option requires a paid API key.

## Evaluation dataset and demo evidence

`eval_cases.json` contains ten repeatable support cases: normal billing, handoff quality, resolved and corrected memories, tenant/user isolation, secret and prompt-injection blocking, small-talk admission, and fast-path latency. Use it as the evidence checklist for the lead engineer.

For a convincing demo, show these five flows in order:

1. Load a Nayatel KB source; ask a billing question and open **Last RAG retrieval** to show source-grounding.
2. Escalate `I was charged twice for invoice INV-42`; show the exact agent packet and full transcript.
3. In the agent inbox mark it `corrected` with `Bank reversal, not a duplicate charge.` Show the queued/complete memory update and SQLite audit result.
4. Sign in again as the same Nayatel customer; show the returning-user welcome and corrected case context. Then sign in as Bob or use Shifa workspace to show no leakage.
5. Run `py -3 -m pytest -q`; use `NEGATIVE_TESTING.md` plus `eval_cases.json` as the regression record.

## Three public tenant test sources

The sidebar button **Load 3 public tenant demo sources** loads these single, public pages into separate workspaces:

| Workspace | Public source | Safe test questions |
|---|---|---|
| `nayatel-demo` | https://nayatel.com/faqs | “How can I pay my bill?” / “My Wi-Fi is slow.” |
| `shifa-demo` | https://www.shifa.com.pk/city/islamabad | “What patient guide information is available?” |
| `general-demo` | https://support.mozilla.org/en-US/kb/what-firefox-account | “What is a Firefox account?” |

Use the same customer wording in each workspace, then open **Last RAG retrieval (test evidence)**. The retrieved sources must belong only to the active workspace. The Shifa source is only a public information/tenant-isolation test: this demo must not diagnose, triage, or make medical decisions.
