"""Contracts for task execution."""

from __future__ import annotations

from typing import Protocol

from core.context import ExecutionContext

from .types import TaskInput, TaskOutput


class TaskHandler(Protocol):
    """Executes one unit of work. Provided by future capability layers."""

    def run(self, input: TaskInput, context: ExecutionContext) -> TaskOutput: ...
