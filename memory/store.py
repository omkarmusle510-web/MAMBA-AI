"""In-memory store implementation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from .errors import InvalidMemoryRequestError, MemoryRetrievalError, MemoryStorageError
from .types import MemoryEntry, MemoryQuery, MemoryResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _metadata_matches(entry_metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    return all(entry_metadata.get(key) == value for key, value in filters.items())


def _content_matches(content: str, query: str) -> bool:
    return query.casefold() in content.casefold()


def _sort_key(entry: MemoryEntry) -> tuple[datetime, str]:
    return (entry.created_at, entry.id)


@dataclass(slots=True)
class InMemoryStore:
    """Simple deterministic in-process memory store."""

    store_name: str = "in_memory"
    _entries: dict[str, MemoryEntry] = field(default_factory=dict, repr=False, compare=False)

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        try:
            existing = self._entries.get(entry.id)
            if existing is None:
                stored = entry
            else:
                stored = replace(
                    entry,
                    created_at=existing.created_at,
                    updated_at=_utc_now(),
                )
            self._entries[stored.id] = stored
            return stored
        except ValueError as exc:
            raise InvalidMemoryRequestError(str(exc)) from exc
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def retrieve(self, query: MemoryQuery) -> MemoryResult:
        try:
            matches = [
                entry
                for entry in self._entries.values()
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
        except ValueError as exc:
            raise InvalidMemoryRequestError(str(exc)) from exc
        except Exception as exc:
            raise MemoryRetrievalError(str(exc)) from exc

    def delete(self, memory_id: str) -> bool:
        if memory_id in self._entries:
            del self._entries[memory_id]
            return True
        return False

    def _matches(self, entry: MemoryEntry, query: MemoryQuery) -> bool:
        text = query.query.strip()
        if text and not _content_matches(entry.content, text):
            return False
        if query.metadata and not _metadata_matches(entry.metadata, query.metadata):
            return False
        return True
