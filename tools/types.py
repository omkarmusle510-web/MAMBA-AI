"""Tool input and output types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Tool:
    """Describes an external capability."""

    name: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.description.strip():
            raise ValueError("description must not be empty")


@dataclass(frozen=True, slots=True)
class ToolInput:
    """Input supplied to a tool invocation."""

    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """Result of a tool invocation."""

    success: bool = True
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
