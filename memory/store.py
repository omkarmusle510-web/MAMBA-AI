"""In-memory store implementation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import re
from typing import Any

from .errors import InvalidMemoryRequestError, MemoryRetrievalError, MemoryStorageError
from .types import MemoryEntry, MemoryQuery, MemoryResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
