"""Task input and output types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.context import ExecutionContext
from core.types import PlanStep


@dataclass(frozen=True, slots=True)
class TaskInput:
    """Information required from Core to execute one unit of work."""

    step_id: str
    description: str
    intent: str
    execution_id: str
    goal: str
    step_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_step(cls, step: PlanStep, context: ExecutionContext) -> TaskInput:
        return cls(
            step_id=step.id,
            description=step.description,
            intent=step.intent,
            execution_id=context.execution_id,
            goal=context.request.goal,
            step_metadata=dict(step.metadata),
        )


@dataclass(frozen=True, slots=True)
class TaskOutput:
    """Result produced by a task that maps to a Core Observation."""

    content: str
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
