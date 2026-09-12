"""SQLite-backed persistent memory store implementation."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import InvalidMemoryRequestError, MemoryRetrievalError, MemoryStorageError
from .types import MemoryEntry, MemoryQuery, MemoryResult

_DEFAULT_DB_PATH = ".mamba/memory.db"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _metadata_matches(entry_metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    return all(entry_metadata.get(key) == value for key, value in filters.items())


def _content_matches(content: str, query: str) -> bool:
    return query.casefold() in content.casefold()


def _sort_key(entry: MemoryEntry) -> tuple[datetime, str]:
    return (entry.created_at, entry.id)


@dataclass(slots=True)
class PersistentStore:
    """Local SQLite-backed persistent memory store conforming to MemoryStore."""

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
        """Initialize database schema and pragmas."""
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
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_created_at
                ON memories (created_at);
                """
            )

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a MemoryEntry persistently. Updates existing entries by id."""
        if not isinstance(entry, MemoryEntry):
            raise InvalidMemoryRequestError(f"expected MemoryEntry, got {type(entry).__name__}")
        assert self._connection is not None

        try:
            now = _utc_now()
            with self._connection:
                cursor = self._connection.execute(
                    "SELECT created_at FROM memories WHERE id = ?",
                    (entry.id,),
                )
                row = cursor.fetchone()
                if row is None:
                    stored = entry
                else:
                    existing_created_at = datetime.fromisoformat(row[0])
                    stored = replace(
                        entry,
                        created_at=existing_created_at,
                        updated_at=now,
                    )

                metadata_json = json.dumps(stored.metadata, ensure_ascii=False)
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO memories (id, content, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        stored.id,
                        stored.content,
                        metadata_json,
                        stored.created_at.isoformat(),
                        stored.updated_at.isoformat(),
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
            cursor = self._connection.execute(
                "SELECT id, content, metadata, created_at, updated_at FROM memories ORDER BY created_at, id"
            )
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

    def delete(self, memory_id: str) -> bool:
        """Delete a memory entry by id. Returns True if deleted, False otherwise."""
        if not memory_id or not isinstance(memory_id, str):
            return False
        assert self._connection is not None

        try:
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (memory_id,),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def _row_to_entry(self, row: tuple[str, str, str, str, str]) -> MemoryEntry:
        """Deserialize a SQLite row into a MemoryEntry."""
        row_id, content, metadata_str, created_at_str, updated_at_str = row
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

