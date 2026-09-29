# Mamba Long-Term Memory

Mamba includes a persistent, hybrid memory system designed to store durable user facts, preferences, project context, and task outcomes across sessions. It is implemented in the `memory/` package and is provider-independent: embeddings run locally and memory never requires an LLM call to function.

---

## 1. Storage & Architecture

- **Engine**: Local SQLite database with Write-Ahead Logging (`WAL` mode) and parameterized queries (`memory/store.py`, `memory/persistent.py`).
- **Location**: `.mamba/memory.db` (configurable via `MAMBA_DB_PATH`).
- **Manager**: `memory/manager.py :: MemoryManager` — the API Core uses: `remember`, `retrieve`, `update`, `supersede`, `forget`, `summarize`, `reindex`, plus content classification (`classify_content`), capture heuristics (`should_capture`), secret filtering, and ephemeral-noise filtering.
- **Semantic Retrieval**: Local vector embeddings via `SentenceTransformerEmbeddingProvider` (`all-MiniLM-L6-v2`), loaded lazily (`memory/embedding.py`, `memory/vector_index.py`).
- **Keyword Fallback**: Token-overlap matching with centralized stopword filtering (`memory/stopwords.py`) when the embedding model is unavailable. Retrieval degrades gracefully — it never fails the request.
- **Provider Independent**: Memory operates fully offline; vector queries are never sent to remote LLM APIs.

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

To keep memory clean and prevent query bloat, Mamba's Brain **automatically filters out transient read/inspection tool output** in `_update_memory`:

- Directory listings (`list_directory`)
- File content reads (`read_file`)
- System info & GPU queries (`system_info`)
- Screenshots & OCR text (`screenshot`, `ocr`)
- Web search query dumps (`web_search`)
- Email/calendar/message listings and project inspection reads

If a plan's steps are *all* transient reads, nothing is persisted. Screen interpretations (`visual_understanding`) are never persisted. Only mutating actions, explicit user facts, and decisions marked durable are saved to SQLite. The `MemoryManager` additionally filters secrets and ephemeral noise at capture time.

---

## 5. Retrieval at Request Time

On every `Brain.run()`, memory retrieval runs **before planning**:

1. `MemoryManager.retrieve(query=goal, project=…, limit=5)` — semantic vector search, keyword fallback.
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
