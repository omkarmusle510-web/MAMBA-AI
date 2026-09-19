# Mamba Long-Term Memory (Memory V2)

Mamba includes a persistent, hybrid memory system (**Memory V2**) designed to store durable user facts, preferences, project context, and task outcomes across sessions.

---

## 1. Storage & Architecture

- **Engine**: Local SQLite database with Write-Ahead Logging (`WAL` mode) and parameterized queries.
- **Location**: `.mamba/memory.db` (configurable via `MAMBA_DB_PATH`).
- **Semantic Retrieval**: Local vector embeddings powered by `sentence-transformers` (`all-MiniLM-L6-v2`) with cosine similarity.
- **Keyword Fallback**: Token-overlap matching with centralized stopword filtering if semantic embeddings are unavailable.
- **Provider Independent**: Memory operates completely offline without sending vector queries to remote LLM APIs.

---

## 2. Memory Types & Statuses

Memory entries are categorized by `MemoryType`:

| Type | Description | Example |
| :--- | :--- | :--- |
| `PREFERENCE` | User tool, editor, or workflow preference | *"My favorite editor is VS Code."* |
| `FACT` | Durable personal or system facts | *"My name is Alice."*, *"I live in Seattle."* |
| `PROJECT_DECISION` | Architectural or technical project decision | *"Use SQLite for persistence, not external vector DBs."* |
| `TASK_CONTEXT` | Durable outcomes from past multi-step executions | *"Completed database migration on branch feature/auth."* |
| `KNOWLEDGE` | General facts or system configurations | *"PostgreSQL is running on port 5432."* |

Entries have a lifecycle status (`active`, `superseded`, `archived`) to ensure outdated facts do not corrupt future reasoning.

---

## 3. Intelligent Supersession

When a user updates a fact, Mamba detects conflict and supersedes the older memory entry rather than accumulating contradictory data:

```text
"My name is Alice" ──► superseded by ──► "My name is Bob"
"I live in Paris"  ──► superseded by ──► "I live in London"
```

The older entry is updated with `status = 'superseded'` and `superseded_by = '<new_id>'`.

---

## 4. Transient Memory Filtering

To keep memory clean and prevent query bloat, Mamba's Brain **automatically filters out transient read/inspection tool output**:

- Directory listings (`list_directory`)
- File content reads (`read_file`)
- System info & GPU queries (`system_info`)
- Screenshots & OCR text (`screenshot`, `ocr`)
- Web search query dumps (`web_search`)

Only mutating actions, explicit user facts, and decisions marked durable are saved to SQLite.

---

## 5. Using Memory

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

