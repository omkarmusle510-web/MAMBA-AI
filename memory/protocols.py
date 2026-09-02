"""Contracts for memory storage."""

from __future__ import annotations

from typing import Protocol

from .types import MemoryEntry, MemoryQuery, MemoryResult


class MemoryStore(Protocol):
    """Stores and retrieves memory entries."""

    def store(self, entry: MemoryEntry) -> MemoryEntry: ...

    def retrieve(self, query: MemoryQuery) -> MemoryResult: ...

    def delete(self, memory_id: str) -> bool: ...
