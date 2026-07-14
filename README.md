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
ollama pull llama3.1:8b
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

- `llama3.1:8b`: LLM that extracts durable memory facts for Mem0.
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
