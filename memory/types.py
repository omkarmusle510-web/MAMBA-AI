"""Memory entry, query, and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One stored memory item."""

    content: str
    id: str = field(default_factory=_new_id)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("id must not be empty")


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """Criteria for retrieving stored memory."""

    query: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    limit: int = 100

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("limit must not be negative")


@dataclass(frozen=True, slots=True)
class MemoryResult:
    """Memory entries returned for a query."""

    entries: tuple[MemoryEntry, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
