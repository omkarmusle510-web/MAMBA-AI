"""Contracts for components that integrate with Core."""

from __future__ import annotations

from typing import Protocol

from .context import ExecutionContext
from .types import ExecutionPlan, Observation, PlanStep


class Planner(Protocol):
    """Produces an execution plan for the current context."""

    def plan(self, context: ExecutionContext) -> ExecutionPlan: ...


class Executor(Protocol):
    """Runs a single plan step and returns an observation."""

    def execute(self, step: PlanStep, context: ExecutionContext) -> Observation: ...
