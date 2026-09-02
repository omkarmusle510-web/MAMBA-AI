"""Shared data types for Mamba's execution lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


class ResultStatus(StrEnum):
    """Terminal outcome of an execution."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UserRequest:
    """A user goal submitted to Mamba."""

    goal: str
    id: str = field(default_factory=_new_id)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("goal must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserRequest:
        return cls(
            goal=data["goal"],
            id=data["id"],
            metadata=dict(data.get("metadata", {})),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    """A single step within an execution plan."""

    description: str
    intent: str
    id: str = field(default_factory=_new_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("description must not be empty")
        if not self.intent.strip():
            raise ValueError("intent must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "intent": self.intent,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(
            description=data["description"],
            intent=data["intent"],
            id=data["id"],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """An ordered plan produced for a user request."""

    steps: tuple[PlanStep, ...]
    id: str = field(default_factory=_new_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("steps must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "steps": [step.to_dict() for step in self.steps],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlan:
        return cls(
            steps=tuple(PlanStep.from_dict(step) for step in data["steps"]),
            id=data["id"],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class Observation:
    """The outcome observed after a plan step runs."""

    step_id: str
    content: str
    success: bool = True
    id: str = field(default_factory=_new_id)
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "step_id": self.step_id,
            "content": self.content,
            "success": self.success,
            "metadata": self.metadata,
            "observed_at": self.observed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        return cls(
            step_id=data["step_id"],
            content=data["content"],
            success=data.get("success", True),
            id=data["id"],
            metadata=dict(data.get("metadata", {})),
            observed_at=datetime.fromisoformat(data["observed_at"]),
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The final outcome returned for a completed execution."""

    execution_id: str
    status: ResultStatus
    goal: str
    observations: tuple[Observation, ...]
    output: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status.value,
            "goal": self.goal,
            "observations": [observation.to_dict() for observation in self.observations],
            "output": self.output,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionResult:
        return cls(
            execution_id=data["execution_id"],
            status=ResultStatus(data["status"]),
            goal=data["goal"],
            observations=tuple(
                Observation.from_dict(observation) for observation in data["observations"]
            ),
            output=data.get("output"),
            error=data.get("error"),
        )
