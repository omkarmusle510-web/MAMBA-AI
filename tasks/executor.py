"""Core Executor adapter for task execution."""

from __future__ import annotations

from dataclasses import dataclass

from core.context import ExecutionContext
from core.types import Observation, PlanStep

from .errors import TaskError
from .protocols import TaskHandler
from .task import Task
from .types import TaskInput, TaskOutput


def _to_observation(step_id: str, output: TaskOutput) -> Observation:
    return Observation(
        step_id=step_id,
        content=output.content,
        success=output.success,
        metadata=dict(output.metadata),
    )


@dataclass(slots=True)
class TaskExecutor:
    """Runs tasks through a handler and returns Core Observations."""

    handler: TaskHandler

    def execute(self, step: PlanStep, context: ExecutionContext) -> Observation:
        task_input = TaskInput.from_step(step, context)
        task = Task.create(task_input)
        task.start()

        try:
            output = self.handler.run(task_input, context)
        except TaskError as exc:
            task.fail(str(exc))
            return Observation(step_id=task_input.step_id, content=str(exc), success=False)

        if output.success:
            task.complete(output)
        else:
            task.fail(output.content or "task failed")
            task.output = output

        return _to_observation(task_input.step_id, output)
