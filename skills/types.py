"""Skill input and output types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput


@dataclass(frozen=True, slots=True)
class SkillInput:
    """Input supplied to a skill, preserving task and execution context by reference."""

    task_input: TaskInput
    context: ExecutionContext

    @classmethod
    def from_task(cls, task_input: TaskInput, context: ExecutionContext) -> SkillInput:
        return cls(task_input=task_input, context=context)


@dataclass(frozen=True, slots=True)
class SkillOutput:
    """Result of skill execution."""

    content: str
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_task_output(self) -> TaskOutput:
        return TaskOutput(
            content=self.content,
            success=self.success,
            metadata=dict(self.metadata),
        )
