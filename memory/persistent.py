"""SQLite-backed persistent memory store implementation."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import InvalidMemoryRequestError, MemoryRetrievalError, MemoryStorageError
from .types import MemoryEntry, MemoryQuery, MemoryResult, MemoryStatus, MemoryType

_DEFAULT_DB_PATH = ".mamba/memory.db"

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
        "at", "by", "for", "with", "about", "against", "between", "into",
        "through", "during", "before", "after", "above", "below", "to", "from",
        "up", "down", "in", "out", "on", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "all", "any", "both",
        "each", "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very", "can",
        "will", "just", "don", "should", "now", "i", "me", "my", "we", "our",
        "you", "your", "he", "him", "his", "she", "her", "it", "its", "they",
        "them", "their", "what", "which", "who", "whom", "this", "that",
        "these", "those", "am", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "having", "do", "does", "did", "doing",
        "tell", "show", "give", "get", "find", "please", "recall", "remember",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _pack_embedding(embedding: list[float] | None) -> bytes | None:
    if not embedding:
        return None
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack_embedding(data: bytes | None) -> list[float] | None:
    if not data:
        return None
    num_floats = len(data) // 4
    return list(struct.unpack(f"{num_floats}f", data))


def _metadata_matches(entry_metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    return all(entry_metadata.get(key) == value for key, value in filters.items())


def _content_matches(content: str, query: str) -> bool:
    content_lower = content.casefold()
    query_lower = query.casefold().strip()
    if not query_lower:
        return True
    if query_lower in content_lower:
        return True

    query_tokens = [
        t for t in re.findall(r"\w+", query_lower)
        if len(t) > 1 and t not in _STOPWORDS
    ]
    if not query_tokens:
        return False

    content_tokens = set(re.findall(r"\w+", content_lower))
    matches = sum(1 for t in query_tokens if t in content_tokens)
    if matches == len(query_tokens):
        return True
    if len(query_tokens) >= 3 and (matches / len(query_tokens)) >= 0.6:
        return True

    return False


def _sort_key(entry: MemoryEntry) -> tuple[datetime, str]:
    return (entry.created_at, entry.id)


@dataclass(slots=True)
class PersistentStore:
    """Local SQLite-backed persistent memory store conforming to MemoryStore and ExtendedMemoryStore."""

    store_name: str = "persistent"
    _db_path: str = _DEFAULT_DB_PATH
    _connection: sqlite3.Connection | None = None

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        store_name: str = "persistent",
    ) -> None:
        self.store_name = store_name
        resolved_path = str(db_path) if db_path is not None else os.environ.get("MAMBA_MEMORY_DB", _DEFAULT_DB_PATH)

        if resolved_path != ":memory:":
            path = Path(resolved_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(path)
        else:
            self._db_path = ":memory:"

        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_db()

    @property
    def db_path(self) -> str:
        """Absolute or logical path to the underlying SQLite database."""
        return self._db_path

    def _init_db(self) -> None:
        """Initialize database schema, migrations, pragmas, and indexes."""
        assert self._connection is not None
        with self._connection:
            try:
                self._connection.execute("PRAGMA journal_mode = WAL;")
            except sqlite3.DatabaseError:
                pass
            try:
                self._connection.execute("PRAGMA synchronous = NORMAL;")
            except sqlite3.DatabaseError:
                pass

            # Base table definition
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    memory_type TEXT NOT NULL DEFAULT 'knowledge',
                    project TEXT NOT NULL DEFAULT '',
                    task TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    importance REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    superseded_by TEXT NOT NULL DEFAULT '',
                    embedding BLOB
                );
                """
            )

            # Schema migration check: safely add any missing columns to existing databases
            cursor = self._connection.execute("PRAGMA table_info(memories);")
            existing_columns = {row[1] for row in cursor.fetchall()}

            new_columns = {
                "memory_type": "TEXT NOT NULL DEFAULT 'knowledge'",
                "project": "TEXT NOT NULL DEFAULT ''",
                "task": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT ''",
                "importance": "REAL NOT NULL DEFAULT 0.5",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "superseded_by": "TEXT NOT NULL DEFAULT ''",
                "embedding": "BLOB",
            }
            for col_name, col_def in new_columns.items():
                if col_name not in existing_columns:
                    try:
                        self._connection.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def};")
                    except sqlite3.OperationalError:
                        pass

            # Indexes
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_created_at
                ON memories (created_at);
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_project
                ON memories (project);
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_status
                ON memories (status);
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_type
                ON memories (memory_type);
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_importance
                ON memories (importance);
                """
            )

    def store(self, entry: MemoryEntry, embedding: list[float] | None = None) -> MemoryEntry:
        """Store a MemoryEntry persistently. Updates existing entries by id.

        Optionally accepts an embedding vector to store alongside the entry.
        """
        if not isinstance(entry, MemoryEntry):
            raise InvalidMemoryRequestError(f"expected MemoryEntry, got {type(entry).__name__}")
        assert self._connection is not None

        try:
            now = _utc_now()
            with self._connection:
                cursor = self._connection.execute(
                    "SELECT created_at, embedding FROM memories WHERE id = ?",
                    (entry.id,),
                )
                row = cursor.fetchone()
                if row is None:
                    stored = entry
                    packed_emb = _pack_embedding(embedding)
                else:
                    existing_created_at = datetime.fromisoformat(row[0])
                    existing_emb = row[1]
                    stored = replace(
                        entry,
                        created_at=existing_created_at,
                        updated_at=now,
                    )
                    packed_emb = _pack_embedding(embedding) if embedding is not None else existing_emb

                metadata_json = json.dumps(stored.metadata, ensure_ascii=False)
                type_val = stored.memory_type.value if isinstance(stored.memory_type, MemoryType) else str(stored.memory_type)
                status_val = stored.status.value if isinstance(stored.status, MemoryStatus) else str(stored.status)

                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO memories (
                        id, content, metadata, created_at, updated_at,
                        memory_type, project, task, source, importance,
                        status, superseded_by, embedding
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stored.id,
                        stored.content,
                        metadata_json,
                        stored.created_at.isoformat(),
                        stored.updated_at.isoformat(),
                        type_val,
                        stored.project,
                        stored.task,
                        stored.source,
                        float(stored.importance),
                        status_val,
                        stored.superseded_by,
                        packed_emb,
                    ),
                )
            return stored
        except (ValueError, TypeError) as exc:
            raise InvalidMemoryRequestError(str(exc)) from exc
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def retrieve(self, query: MemoryQuery) -> MemoryResult:
        """Retrieve memory entries matching query criteria."""
        if not isinstance(query, MemoryQuery):
            raise InvalidMemoryRequestError(f"expected MemoryQuery, got {type(query).__name__}")
        assert self._connection is not None

        try:
            sql = "SELECT id, content, metadata, created_at, updated_at, memory_type, project, task, source, importance, status, superseded_by FROM memories WHERE 1=1"
            params: list[Any] = []

            # Status filter
            if query.status is not None:
                status_val = query.status.value if isinstance(query.status, MemoryStatus) else str(query.status)
                sql += " AND status = ?"
                params.append(status_val)

            # Project filter
            if query.project.strip():
                sql += " AND project = ?"
                params.append(query.project.strip())

            # Task filter
            if query.task.strip():
                sql += " AND task = ?"
                params.append(query.task.strip())

            # Memory type filter
            if query.memory_type is not None:
                type_val = query.memory_type.value if isinstance(query.memory_type, MemoryType) else str(query.memory_type)
                sql += " AND memory_type = ?"
                params.append(type_val)

            # Minimum importance filter
            if query.importance_min > 0.0:
                sql += " AND importance >= ?"
                params.append(float(query.importance_min))

            sql += " ORDER BY created_at, id"

            cursor = self._connection.execute(sql, params)
            rows = cursor.fetchall()
            entries = [self._row_to_entry(row) for row in rows]
            matches = [
                entry
                for entry in entries
                if self._matches(entry, query)
            ]
            matches.sort(key=_sort_key)
            limited = matches[: query.limit]
            return MemoryResult(
                entries=tuple(limited),
                metadata={
                    "store": self.store_name,
                    "matched": len(matches),
                    "returned": len(limited),
                },
            )
        except MemoryRetrievalError:
            raise
        except ValueError as exc:
            raise InvalidMemoryRequestError(str(exc)) from exc
        except Exception as exc:
            raise MemoryRetrievalError(str(exc)) from exc

    def update(self, memory_id: str, **kwargs: Any) -> MemoryEntry | None:
        """Update an existing memory entry in place."""
        if not memory_id or not isinstance(memory_id, str):
            return None
        assert self._connection is not None

        try:
            cursor = self._connection.execute(
                "SELECT id, content, metadata, created_at, updated_at, memory_type, project, task, source, importance, status, superseded_by FROM memories WHERE id = ?",
                (memory_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            entry = self._row_to_entry(row)
            now = _utc_now()

            new_content = kwargs.get("content", entry.content)
            new_metadata = dict(kwargs.get("metadata", entry.metadata))
            new_type = kwargs.get("memory_type", entry.memory_type)
            new_project = kwargs.get("project", entry.project)
            new_task = kwargs.get("task", entry.task)
            new_source = kwargs.get("source", entry.source)
            new_importance = kwargs.get("importance", entry.importance)
            new_status = kwargs.get("status", entry.status)
            new_superseded_by = kwargs.get("superseded_by", entry.superseded_by)

            updated_entry = MemoryEntry(
                id=entry.id,
                content=str(new_content),
                metadata=new_metadata,
                created_at=entry.created_at,
                updated_at=now,
                memory_type=new_type,
                project=str(new_project),
                task=str(new_task),
                source=str(new_source),
                importance=float(new_importance),
                status=new_status,
                superseded_by=str(new_superseded_by),
            )

            new_emb = kwargs.get("embedding", None)
            with self._connection:
                if new_emb is not None:
                    packed_emb = _pack_embedding(new_emb)
                    self._connection.execute(
                        """
                        UPDATE memories SET
                            content = ?, metadata = ?, updated_at = ?,
                            memory_type = ?, project = ?, task = ?, source = ?,
                            importance = ?, status = ?, superseded_by = ?, embedding = ?
                        WHERE id = ?
                        """,
                        (
                            updated_entry.content,
                            json.dumps(updated_entry.metadata, ensure_ascii=False),
                            updated_entry.updated_at.isoformat(),
                            updated_entry.memory_type.value if isinstance(updated_entry.memory_type, MemoryType) else str(updated_entry.memory_type),
                            updated_entry.project,
                            updated_entry.task,
                            updated_entry.source,
                            float(updated_entry.importance),
                            updated_entry.status.value if isinstance(updated_entry.status, MemoryStatus) else str(updated_entry.status),
                            updated_entry.superseded_by,
                            packed_emb,
                            memory_id,
                        ),
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE memories SET
                            content = ?, metadata = ?, updated_at = ?,
                            memory_type = ?, project = ?, task = ?, source = ?,
                            importance = ?, status = ?, superseded_by = ?
                        WHERE id = ?
                        """,
                        (
                            updated_entry.content,
                            json.dumps(updated_entry.metadata, ensure_ascii=False),
                            updated_entry.updated_at.isoformat(),
                            updated_entry.memory_type.value if isinstance(updated_entry.memory_type, MemoryType) else str(updated_entry.memory_type),
                            updated_entry.project,
                            updated_entry.task,
                            updated_entry.source,
                            float(updated_entry.importance),
                            updated_entry.status.value if isinstance(updated_entry.status, MemoryStatus) else str(updated_entry.status),
                            updated_entry.superseded_by,
                            memory_id,
                        ),
                    )

            return updated_entry
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def delete(self, memory_id: str, *, hard_delete: bool = False) -> bool:
        """Delete a memory entry by id.

        By default performs a soft delete (status='deleted', embedding=NULL)
        to invalidate vector indices. Set hard_delete=True to remove completely.
        """
        if not memory_id or not isinstance(memory_id, str):
            return False
        assert self._connection is not None

        try:
            now = _utc_now()
            with self._connection:
                if hard_delete:
                    cursor = self._connection.execute(
                        "DELETE FROM memories WHERE id = ?",
                        (memory_id,),
                    )
                else:
                    cursor = self._connection.execute(
                        """
                        UPDATE memories
                        SET status = 'deleted', updated_at = ?, embedding = NULL
                        WHERE id = ?
                        """,
                        (now.isoformat(), memory_id),
                    )
                return cursor.rowcount > 0
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def find_related(self, content: str, project: str = "", limit: int = 5) -> list[MemoryEntry]:
        """Find active memories related to given content within a project for supersession or context."""
        query = MemoryQuery(
            query=content,
            project=project,
            status=MemoryStatus.ACTIVE,
            limit=limit,
        )
        res = self.retrieve(query)
        return list(res.entries)

    def store_embedding(self, memory_id: str, embedding: list[float]) -> bool:
        """Store or update vector embedding for an existing memory item."""
        if not memory_id or not embedding:
            return False
        assert self._connection is not None
        try:
            packed = _pack_embedding(embedding)
            with self._connection:
                cursor = self._connection.execute(
                    "UPDATE memories SET embedding = ? WHERE id = ?",
                    (packed, memory_id),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def get_embedding(self, memory_id: str) -> list[float] | None:
        """Retrieve embedding vector for an entry."""
        if not memory_id:
            return None
        assert self._connection is not None
        cursor = self._connection.execute(
            "SELECT embedding FROM memories WHERE id = ?",
            (memory_id,),
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            return _unpack_embedding(row[0])
        return None

    def get_all_embeddings(
        self,
        project: str = "",
        memory_type: str | None = None,
        status: str | None = "active",
    ) -> list[tuple[str, list[float]]]:
        """Load all non-null embeddings matching criteria."""
        assert self._connection is not None
        sql = "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
        params: list[Any] = []

        if status:
            sql += " AND status = ?"
            params.append(status)
        if project:
            sql += " AND project = ?"
            params.append(project)
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)

        cursor = self._connection.execute(sql, params)
        results: list[tuple[str, list[float]]] = []
        for row in cursor.fetchall():
            vec = _unpack_embedding(row[1])
            if vec is not None:
                results.append((row[0], vec))
        return results

    def _row_to_entry(self, row: tuple[Any, ...]) -> MemoryEntry:
        """Deserialize a SQLite row into a MemoryEntry."""
        (
            row_id,
            content,
            metadata_str,
            created_at_str,
            updated_at_str,
            memory_type_str,
            project,
            task,
            source,
            importance,
            status_str,
            superseded_by,
        ) = row[:12]

        try:
            metadata = json.loads(metadata_str)
            if not isinstance(metadata, dict):
                raise ValueError("deserialized metadata must be a dictionary")
            created_at = datetime.fromisoformat(created_at_str)
            updated_at = datetime.fromisoformat(updated_at_str)
            return MemoryEntry(
                id=row_id,
                content=content,
                metadata=metadata,
                created_at=created_at,
                updated_at=updated_at,
                memory_type=memory_type_str,
                project=project or "",
                task=task or "",
                source=source or "",
                importance=float(importance) if importance is not None else 0.5,
                status=status_str or "active",
                superseded_by=superseded_by or "",
            )
        except Exception as exc:
            raise MemoryRetrievalError(
                f"failed to deserialize memory entry '{row_id}': {exc}"
            ) from exc

    def _matches(self, entry: MemoryEntry, query: MemoryQuery) -> bool:
        """Evaluate deterministic query match semantics."""
        text = query.query.strip()
        if text and not _content_matches(entry.content, text):
            return False
        if query.metadata and not _metadata_matches(entry.metadata, query.metadata):
            return False
        return True

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def __enter__(self) -> PersistentStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
