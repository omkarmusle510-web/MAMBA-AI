"""Execution lifecycle coordination."""

from __future__ import annotations

from dataclasses import dataclass

from .context import ExecutionContext
from .errors import CoreError
from .protocols import Executor, Planner
from .state import ExecutionState
from .types import ExecutionResult, UserRequest


def _last_observation_content(context: ExecutionContext) -> str | None:
    observations = context.observations
    if not observations:
        return None
    return observations[-1].content


@dataclass(slots=True)
class Orchestrator:
    """Coordinates request lifecycle through planning and execution."""

    planner: Planner
    executor: Executor

    def run(self, request: UserRequest) -> ExecutionResult:
        context = ExecutionContext.from_request(request)
        context.transition_to(ExecutionState.PLANNING)

        try:
            plan = self.planner.plan(context)
            context.attach_plan(plan)
            context.transition_to(ExecutionState.EXECUTING)
        except CoreError as exc:
            context.mark_failed(str(exc))
            return context.record.to_result()

        for step in plan.steps:
            try:
                observation = self.executor.execute(step, context)
                context.add_observation(observation)
            except CoreError as exc:
                context.mark_failed(str(exc))
                return context.record.to_result()

            if not observation.success:
                context.mark_failed(observation.content or "step failed")
                return context.record.to_result()

        context.transition_to(ExecutionState.COMPLETED)
        return context.record.to_result(output=_last_observation_content(context))
