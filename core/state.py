"""Execution state and lifecycle record."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .errors import StateTransitionError, ValidationError
from .types import ExecutionPlan, ExecutionResult, Observation, ResultStatus, UserRequest


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ExecutionState(StrEnum):
    """Lifecycle phase of an in-flight execution."""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


_TERMINAL_STATES = frozenset({ExecutionState.COMPLETED, ExecutionState.FAILED})

_ALLOWED_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PENDING: frozenset({ExecutionState.PLANNING, ExecutionState.FAILED}),
    ExecutionState.PLANNING: frozenset({ExecutionState.EXECUTING, ExecutionState.FAILED}),
    ExecutionState.EXECUTING: frozenset({ExecutionState.COMPLETED, ExecutionState.FAILED}),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.FAILED: frozenset(),
}


@dataclass(slots=True)
class ExecutionRecord:
    """Mutable record tracking one execution from start to finish."""

    request: UserRequest
    id: str
    state: ExecutionState = ExecutionState.PENDING
    plan: ExecutionPlan | None = None
    observations: list[Observation] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime | None = None

    @classmethod
    def create(cls, request: UserRequest, *, execution_id: str | None = None) -> ExecutionRecord:
        return cls(request=request, id=execution_id or request.id)

    def transition_to(self, new_state: ExecutionState) -> None:
        allowed = _ALLOWED_TRANSITIONS[self.state]
        if new_state not in allowed:
            raise StateTransitionError(
                f"cannot transition from {self.state.value} to {new_state.value}"
            )

        now = _utc_now()
        self.state = new_state
        self.updated_at = now
        if new_state in _TERMINAL_STATES:
            self.completed_at = now

    def attach_plan(self, plan: ExecutionPlan) -> None:
        if self.plan is not None:
            raise ValidationError("execution already has a plan")
        self.plan = plan
        self.updated_at = _utc_now()

    def add_observation(self, observation: Observation) -> None:
        self.observations.append(observation)
        self.updated_at = _utc_now()

    def mark_failed(self, error: str) -> None:
        self.error = error
        self.transition_to(ExecutionState.FAILED)

    def to_result(self, *, output: str | None = None) -> ExecutionResult:
        if self.state == ExecutionState.COMPLETED:
            status = ResultStatus.COMPLETED
        elif self.state == ExecutionState.FAILED:
            status = ResultStatus.FAILED
        else:
            raise ValidationError(
                f"cannot build result while execution is in state {self.state.value}"
            )

        return ExecutionResult(
            execution_id=self.id,
            status=status,
            goal=self.request.goal,
            observations=tuple(self.observations),
            output=output,
            error=self.error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "request": self.request.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "observations": [observation.to_dict() for observation in self.observations],
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionRecord:
        return cls(
            request=UserRequest.from_dict(data["request"]),
            id=data["id"],
            state=ExecutionState(data["state"]),
            plan=ExecutionPlan.from_dict(data["plan"]) if data.get("plan") else None,
            observations=[Observation.from_dict(item) for item in data.get("observations", [])],
            error=data.get("error"),
            started_at=datetime.fromisoformat(data["started_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
        )
