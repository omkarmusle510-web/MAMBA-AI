"""Skill definition and base abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput

from .protocols import SkillHandler
from .types import SkillInput, SkillOutput


@dataclass(frozen=True, slots=True)
class Skill:
    """One reusable Mamba capability."""

    name: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.description.strip():
            raise ValueError("description must not be empty")


class BaseSkill(ABC):
    """Minimal common structure for concrete skills."""

    def __init__(self, skill: Skill) -> None:
        self._skill = skill

    @property
    def skill(self) -> Skill:
        return self._skill

    @abstractmethod
    def execute(self, input: SkillInput) -> SkillOutput: ...

    def run(self, input: SkillInput) -> SkillOutput:
        return self.execute(input)


@dataclass(slots=True)
class SkillTaskHandler:
    """Adapts a SkillHandler to the Tasks-layer TaskHandler contract."""

    handler: SkillHandler

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        return self.handler.run(skill_input).to_task_output()
