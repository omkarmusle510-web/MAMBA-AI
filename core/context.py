"""Execution context passed between Core components and future layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .state import ExecutionRecord, ExecutionState
from .types import ExecutionPlan, Observation, UserRequest


@dataclass(slots=True)
class ExecutionContext:
    """Read-oriented view of an execution with explicit mutation entry points."""

    record: ExecutionRecord

    @classmethod
    def from_request(cls, request: UserRequest, *, execution_id: str | None = None) -> ExecutionContext:
        return cls(record=ExecutionRecord.create(request, execution_id=execution_id))

    @property
    def execution_id(self) -> str:
        return self.record.id

    @property
    def request(self) -> UserRequest:
        return self.record.request

    @property
    def plan(self) -> ExecutionPlan | None:
        return self.record.plan

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self.record.observations)

    @property
    def state(self) -> ExecutionState:
        return self.record.state

    def attach_plan(self, plan: ExecutionPlan) -> None:
        self.record.attach_plan(plan)

    def add_observation(self, observation: Observation) -> None:
        if self.record.plan is None:
            raise ValidationError("cannot add observations before a plan is attached")
        self.record.add_observation(observation)

    def transition_to(self, state: ExecutionState) -> None:
        self.record.transition_to(state)

    def mark_failed(self, error: str) -> None:
        self.record.mark_failed(error)

    def to_dict(self) -> dict[str, Any]:
        return {"record": self.record.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionContext:
        return cls(record=ExecutionRecord.from_dict(data["record"]))
