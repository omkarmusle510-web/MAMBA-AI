# Mamba Long-Term Memory

Mamba includes a persistent, hybrid memory system designed to store durable user facts, preferences, project context, and task outcomes across sessions. It is implemented in the `memory/` package and is provider-independent: embeddings run locally and memory never requires an LLM call to function.

> **Status: IMPLEMENTED.** Retrieval, capture, supersession, secret filtering, and transient
> filtering run on every `Brain.run()` and are covered by `tests/test_memory_v2.py`. The one
> environment caveat is the embedding dependency (§1.1) — without it, memory still works, by
> keyword.

---

## 1. Storage & Architecture

- **Engine**: Local SQLite database with Write-Ahead Logging (`WAL` mode) and parameterized queries (`memory/store.py`, `memory/persistent.py`).
- **Location**: `.mamba/memory.db` (configurable via `MAMBA_MEMORY_DB`).
- **Manager**: `memory/manager.py :: MemoryManager` — the API Core uses: `remember`, `retrieve`, `update`, `supersede`, `forget`, `summarize`, `reindex`, plus content classification (`classify_content`), capture heuristics (`should_capture`), secret filtering, and ephemeral-noise filtering.
- **Semantic Retrieval**: Local vector embeddings via `SentenceTransformerEmbeddingProvider` (`all-MiniLM-L6-v2`), loaded lazily and attempted only once (`memory/embedding.py`, `memory/vector_index.py`, `memory/retrieval.py`).
- **Keyword Fallback**: Token-overlap matching with centralized stopword filtering (`memory/stopwords.py`) when the embedding model is unavailable. Retrieval degrades gracefully — it never fails the request.
- **Provider Independent**: Memory operates fully offline; vector queries are never sent to remote LLM APIs.
- **Threading**: the SQLite connection opens with `check_same_thread=False`, so a transport worker thread can share the store instance.

### 1.1 The embedding dependency is not in `requirements.txt`

`sentence-transformers` is **not** listed in `requirements.txt`, so a fresh install uses
keyword retrieval until it is added deliberately:

```powershell
pip install sentence-transformers
```

The model loads lazily on first use and any import or load failure is logged and absorbed —
semantic search is simply off, with no change to any other capability.

---

## 2. Memory Types & Statuses

Memory entries are categorized by `MemoryType` (`memory/types.py`):

| Type | Value | Example |
| :--- | :--- | :--- |
| `USER_PREFERENCE` | `user_preference` | *"My favorite editor is VS Code."* |
| `USER_FACT` | `user_fact` | *"My name is Alice."*, *"I live in Seattle."* |
| `PROJECT_CONTEXT` | `project_context` | *"Auth service lives in services/auth/."* |
| `PROJECT_DECISION` | `project_decision` | *"Use SQLite for persistence, not external vector DBs."* |
| `TASK_CONTEXT` | `task_context` | *"Completed database migration on branch feature/auth."* |
| `KNOWLEDGE` | `knowledge` | *"PostgreSQL is running on port 5432."* |
| `CONVERSATION_SUMMARY` | `conversation_summary` | Compacted summaries of past sessions. |

Entries carry a lifecycle status (`MemoryStatus`):

| Status | Meaning |
| :--- | :--- |
| `active` | Current truth; returned by retrieval. |
| `superseded` | Replaced by a newer entry (linked via `superseded_by`). Excluded from retrieval. |
| `deleted` | Soft-deleted via `forget()` (hard delete optional). Excluded from retrieval. |

Each entry also records `project`, `source`, `importance` (0.0–1.0), timestamps, and free-form `metadata`.

---

## 3. Intelligent Supersession

When a user updates a fact, Mamba detects the conflict and supersedes the older entry rather than accumulating contradictory data:

```text
"My name is Alice" ──► superseded by ──► "My name is Bob"
"I live in Paris"  ──► superseded by ──► "I live in London"
```

The older entry is updated with `status = 'superseded'` and `superseded_by = '<new_id>'`.

---

## 4. Transient Memory Filtering

To keep memory clean and prevent query bloat, Mamba's Brain filters transient read/inspection
output in `_update_memory`, using two explicit intent sets (`core/brain.py`):

- **`_TRANSIENT_INTENTS`** — output that is only a glimpse of current state: `list_directory`,
  `read_file`, `system_info`, `gpu_info`, `screenshot`, `region_screenshot`, `ocr`,
  `region_ocr`, `visual_understanding`, window/clipboard reads, `web_search`,
  email/calendar/message listings and reads, and every `project_understanding` intent.
- **`_MUTATING_INTENTS`** — output that describes a real change and *is* worth persisting:
  `write_file`, `create_directory`, `delete`, `run_command`/`execute_command`, `remember`,
  `send_email`/`reply_email`, `send_message`/`reply_message`, `create_event`/`modify_event`/
  `cancel_event`, and cross-application launches and typing.

Rules:

1. If **every** step of a plan is transient, nothing is persisted — unless the observation or
   the request explicitly marks it durable (`durable_memory` / `persist_memory`).
2. Screen interpretations (`visual_understanding`) are never persisted, regardless of flag.
3. `MemoryManager.should_capture()` additionally rejects secrets, credentials, and trivial
   chatter at capture time. Content matching a password/secret pattern is refused **even when
   storage is explicitly forced**, and the rejection is logged.
4. Default importance is derived from the classified type (user facts and preferences rank
   highest at 0.8/0.7, project context 0.6, everything else 0.5), clamped to 0.0–1.0.

---

## 5. Retrieval at Request Time

On every `Brain.run()`, memory retrieval runs **before planning**:

1. `MemoryManager.retrieve(query=goal, project=…, limit=5)` — semantic vector search with a keyword token-overlap fallback, plus an `importance_min` filter (default `0.0`).
2. Retrieved entries are attached to the request metadata as `retrieved_memories`.
3. The planner sees them as context; project discovery (`core/project.py`) adds `project_context` alongside.

Memory retrieval failure never crashes execution — the request proceeds without recalled context.

---

## 6. Using Memory

### Explicit Storage
```text
mamba> Remember that our production server port is 8080.
[Status: completed]
I've stored that in long-term memory.
```

### Contextual Recall
```text
mamba> What is the production server port?
[Status: completed]
The production server port is 8080.
```

### Through the capability layer

Memory is capability #9 in the registry (`memory`, provider `sqlite`) with actions
`remember`, `recall`, `delete_memory` — so a spoken or typed request reaches it through the
same intent → skill → tool path as every other capability, and the same permission gate
applies (all three are LOW risk; deleting a memory is explicit and irreversible from the
agent's point of view, though the store soft-deletes). See
[SKILLS.md](SKILLS.md) §3.9.

---

## 7. Boundaries and known limitations

| | |
| :--- | :--- |
| **Single user, local machine** | There is no user scope, no encryption at rest, and no sync. The database is protected by the OS account's file permissions and nothing else. |
| **Not a vector database** | Vectors live in the same SQLite file with an in-process index (`memory/vector_index.py`). This is deliberate: no external service, no embedding API call, nothing that leaves the machine. It is not built for millions of entries. |
| **Write is on the response path** | `_update_memory` runs synchronously inside `Brain.run()`, before the final response is returned. Memory capture therefore adds latency to the turn it belongs to; moving it off the critical path is a known, tracked performance item, not a documented behavior. |
| **Embeddings optional** | Without `sentence-transformers` (§1.1) retrieval is keyword-only, which misses paraphrases that a vector search would catch. |
| **Classification is heuristic** | `classify_content` and `should_capture` are rule-based. A fact phrased like chatter can be skipped; an explicit `remember` request overrides the heuristic. |
| **Supersession is conflict-detected, not deduplicated** | Contradictions are resolved by superseding the older entry; near-duplicates that do not conflict can coexist. `summarize()` and `reindex()` exist for compaction. |
| **Secrets are rejected, not redacted** | Content that looks like `password = "…"` is refused at capture. That is a guardrail on what enters memory — it does not scrub secrets that arrive through another capability. |
