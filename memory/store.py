"""In-memory store implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from .errors import InvalidMemoryRequestError, MemoryRetrievalError, MemoryStorageError
from .stopwords import STOPWORDS
from .types import MemoryEntry, MemoryQuery, MemoryResult, MemoryStatus, MemoryType


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
        if len(t) > 1 and t not in STOPWORDS
    ]
    # Safe fallback if stopword filtering stripped all tokens (e.g. "Who am I?", "Tell me about this")
    if not query_tokens:
        query_tokens = [t for t in re.findall(r"\w+", query_lower) if len(t) > 1]
    if not query_tokens:
        return query_lower in content_lower

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
    """Simple deterministic in-process memory store supporting MemoryStore and ExtendedMemoryStore."""

    store_name: str = "in_memory"
    _entries: dict[str, MemoryEntry] = field(default_factory=dict, repr=False, compare=False)
    _embeddings: dict[str, list[float]] = field(default_factory=dict, repr=False, compare=False)

    def store(self, entry: MemoryEntry, embedding: list[float] | None = None) -> MemoryEntry:
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
            if embedding is not None:
                self._embeddings[stored.id] = list(embedding)
            return stored
        except ValueError as exc:
            raise InvalidMemoryRequestError(str(exc)) from exc
        except Exception as exc:
            raise MemoryStorageError(str(exc)) from exc

    def retrieve(self, query: MemoryQuery) -> MemoryResult:
        try:
            candidates: list[MemoryEntry] = []
            for entry in self._entries.values():
                # Status filter
                if query.status is not None:
                    entry_status = entry.status.value if isinstance(entry.status, MemoryStatus) else str(entry.status)
                    query_status = query.status.value if isinstance(query.status, MemoryStatus) else str(query.status)
                    if entry_status != query_status:
                        continue

                # Project filter
                if query.project.strip() and entry.project != query.project.strip():
                    continue

                # Task filter
                if query.task.strip() and entry.task != query.task.strip():
                    continue

                # Memory type filter
                if query.memory_type is not None:
                    entry_type = entry.memory_type.value if isinstance(entry.memory_type, MemoryType) else str(entry.memory_type)
                    query_type = query.memory_type.value if isinstance(query.memory_type, MemoryType) else str(query.memory_type)
                    if entry_type != query_type:
                        continue

                # Minimum importance
                if query.importance_min > 0.0 and entry.importance < query.importance_min:
                    continue

                if self._matches(entry, query):
                    candidates.append(entry)

            candidates.sort(key=_sort_key)
            limited = candidates[: query.limit]
            return MemoryResult(
                entries=tuple(limited),
                metadata={
                    "store": self.store_name,
                    "matched": len(candidates),
                    "returned": len(limited),
                },
            )
        except ValueError as exc:
            raise InvalidMemoryRequestError(str(exc)) from exc
        except Exception as exc:
            raise MemoryRetrievalError(str(exc)) from exc

    def update(self, memory_id: str, **kwargs: Any) -> MemoryEntry | None:
        existing = self._entries.get(memory_id)
        if existing is None:
            return None

        now = _utc_now()
        new_content = kwargs.get("content", existing.content)
        new_metadata = dict(kwargs.get("metadata", existing.metadata))
        new_type = kwargs.get("memory_type", existing.memory_type)
        new_project = kwargs.get("project", existing.project)
        new_task = kwargs.get("task", existing.task)
        new_source = kwargs.get("source", existing.source)
        new_importance = kwargs.get("importance", existing.importance)
        new_status = kwargs.get("status", existing.status)
        new_superseded_by = kwargs.get("superseded_by", existing.superseded_by)

        updated_entry = MemoryEntry(
            id=existing.id,
            content=str(new_content),
            metadata=new_metadata,
            created_at=existing.created_at,
            updated_at=now,
            memory_type=new_type,
            project=str(new_project),
            task=str(new_task),
            source=str(new_source),
            importance=float(new_importance),
            status=new_status,
            superseded_by=str(new_superseded_by),
        )
        self._entries[memory_id] = updated_entry

        if "embedding" in kwargs:
            new_emb = kwargs["embedding"]
            if new_emb is not None:
                self._embeddings[memory_id] = list(new_emb)
            elif memory_id in self._embeddings:
                del self._embeddings[memory_id]

        return updated_entry

    def delete(self, memory_id: str, *, hard_delete: bool = False) -> bool:
        if memory_id not in self._entries:
            return False
        if hard_delete:
            del self._entries[memory_id]
            self._embeddings.pop(memory_id, None)
        else:
            existing = self._entries[memory_id]
            self._entries[memory_id] = replace(
                existing,
                status=MemoryStatus.DELETED,
                updated_at=_utc_now(),
            )
            self._embeddings.pop(memory_id, None)
        return True

    def find_related(self, content: str, project: str = "", limit: int = 5) -> list[MemoryEntry]:
        content_tokens = [
            t for t in re.findall(r"\w+", content.casefold())
            if len(t) > 1 and t not in STOPWORDS
        ]
        if not content_tokens:
            content_tokens = [t for t in re.findall(r"\w+", content.casefold()) if len(t) > 1]

        token_set = set(content_tokens)
        scored: list[tuple[int, MemoryEntry]] = []
        for entry in self._entries.values():
            if entry.status != MemoryStatus.ACTIVE:
                continue
            if project.strip() and entry.project != project.strip():
                continue
            entry_tokens = set(re.findall(r"\w+", entry.content.casefold()))
            overlap = len(token_set & entry_tokens)
            if overlap > 0 or not token_set:
                scored.append((overlap, entry))

        scored.sort(key=lambda x: (x[0], x[1].created_at), reverse=True)
        return [item[1] for item in scored[:limit]]

    def store_embedding(self, memory_id: str, embedding: list[float]) -> bool:
        if memory_id not in self._entries or not embedding:
            return False
        self._embeddings[memory_id] = list(embedding)
        return True

    def get_embedding(self, memory_id: str) -> list[float] | None:
        return self._embeddings.get(memory_id)

    def get_all_embeddings(
        self,
        project: str = "",
        memory_type: str | None = None,
        status: str | None = "active",
    ) -> list[tuple[str, list[float]]]:
        results: list[tuple[str, list[float]]] = []
        for mid, emb in self._embeddings.items():
            entry = self._entries.get(mid)
            if entry is None:
                continue
            entry_status = entry.status.value if isinstance(entry.status, MemoryStatus) else str(entry.status)
            if status and entry_status != status:
                continue
            if project and entry.project != project:
                continue
            if memory_type:
                entry_type = entry.memory_type.value if isinstance(entry.memory_type, MemoryType) else str(entry.memory_type)
                if entry_type != memory_type:
                    continue
            results.append((mid, list(emb)))
        return results

    def _matches(self, entry: MemoryEntry, query: MemoryQuery) -> bool:
        text = query.query.strip()
        if text and not _content_matches(entry.content, text):
            return False
        if query.metadata and not _metadata_matches(entry.metadata, query.metadata):
            return False
        return True
