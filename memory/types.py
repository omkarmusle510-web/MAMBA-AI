"""Memory entry, query, and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class MemoryType(str, Enum):
    """Semantic category of a stored memory item."""

    USER_PREFERENCE = "user_preference"
    USER_FACT = "user_fact"
    PROJECT_CONTEXT = "project_context"
    PROJECT_DECISION = "project_decision"
    TASK_CONTEXT = "task_context"
    KNOWLEDGE = "knowledge"
    CONVERSATION_SUMMARY = "conversation_summary"


class MemoryStatus(str, Enum):
    """Lifecycle status of a stored memory item."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One stored memory item."""

    content: str
    id: str = field(default_factory=_new_id)
    memory_type: MemoryType | str = MemoryType.KNOWLEDGE
    project: str = ""
    task: str = ""
    source: str = ""
    importance: float = 0.5
    status: MemoryStatus | str = MemoryStatus.ACTIVE
    superseded_by: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("id must not be empty")
        if not (0.0 <= self.importance <= 1.0):
            raise ValueError(f"importance must be between 0.0 and 1.0, got {self.importance}")
        if isinstance(self.memory_type, str) and not isinstance(self.memory_type, MemoryType):
            try:
                object.__setattr__(self, "memory_type", MemoryType(self.memory_type))
            except ValueError:
                pass
        if isinstance(self.status, str) and not isinstance(self.status, MemoryStatus):
            try:
                object.__setattr__(self, "status", MemoryStatus(self.status))
            except ValueError:
                pass


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """Criteria for retrieving stored memory."""

    query: str = ""
    memory_type: MemoryType | str | None = None
    project: str = ""
    task: str = ""
    status: MemoryStatus | str | None = MemoryStatus.ACTIVE
    importance_min: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    limit: int = 100
    include_semantic: bool = True

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("limit must not be negative")
        if not (0.0 <= self.importance_min <= 1.0):
            raise ValueError("importance_min must be between 0.0 and 1.0")
        if isinstance(self.memory_type, str) and not isinstance(self.memory_type, MemoryType):
            try:
                object.__setattr__(self, "memory_type", MemoryType(self.memory_type))
            except ValueError:
                pass
        if isinstance(self.status, str) and not isinstance(self.status, MemoryStatus):
            try:
                object.__setattr__(self, "status", MemoryStatus(self.status))
            except ValueError:
                pass


@dataclass(frozen=True, slots=True)
class MemoryResult:
    """Memory entries returned for a query."""

    entries: tuple[MemoryEntry, ...] = ()
    scores: tuple[float, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
