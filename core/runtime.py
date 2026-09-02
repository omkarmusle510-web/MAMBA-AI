"""Public Core entry point."""

from __future__ import annotations

from dataclasses import dataclass

from .orchestrator import Orchestrator
from .protocols import Executor, Planner
from .types import ExecutionResult, UserRequest


@dataclass(slots=True)
class MambaRuntime:
    """Entry point for running a request through the Core lifecycle."""

    planner: Planner
    executor: Executor

    def run(self, request: str | UserRequest) -> ExecutionResult:
        user_request = UserRequest(goal=request) if isinstance(request, str) else request
        return Orchestrator(planner=self.planner, executor=self.executor).run(user_request)
