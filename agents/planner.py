"""Core Planner adapter for agent reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from core.context import ExecutionContext
from core.errors import PlanningError
from core.types import ExecutionPlan

from .protocols import AgentHandler
from .types import AgentInput


@dataclass(slots=True)
class AgentPlanner:
    """Adapts an AgentHandler to the Core Planner contract."""

    handler: AgentHandler

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        output = self.handler.reason(AgentInput.from_context(context))
        if not output.success:
            raise PlanningError(output.error or "agent planning failed")
        if output.plan is None:
            raise PlanningError("agent produced no execution plan")
        return output.plan
