"""Contracts for memory storage."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .types import MemoryEntry, MemoryQuery, MemoryResult


@runtime_checkable
class MemoryStore(Protocol):
    """Stores and retrieves memory entries."""

    def store(self, entry: MemoryEntry) -> MemoryEntry: ...

    def retrieve(self, query: MemoryQuery) -> MemoryResult: ...

    def delete(self, memory_id: str) -> bool: ...


@runtime_checkable
class ExtendedMemoryStore(MemoryStore, Protocol):
    """Extended memory store with updates, relationship search, and status awareness."""

    def update(self, memory_id: str, **kwargs: Any) -> MemoryEntry | None: ...

    def find_related(self, content: str, project: str = "", limit: int = 5) -> list[MemoryEntry]: ...
