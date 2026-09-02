"""Agent input and output types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.context import ExecutionContext
from core.types import ExecutionPlan


@dataclass(frozen=True, slots=True)
class Agent:
    """One reasoning/planning capability."""

    name: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.description.strip():
            raise ValueError("description must not be empty")


@dataclass(frozen=True, slots=True)
class AgentInput:
    """Input supplied to an agent, preserving execution context by reference."""

    context: ExecutionContext

    @classmethod
    def from_context(cls, context: ExecutionContext) -> AgentInput:
        return cls(context=context)


@dataclass(frozen=True, slots=True)
class AgentOutput:
    """Result of agent reasoning."""

    plan: ExecutionPlan | None = None
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
