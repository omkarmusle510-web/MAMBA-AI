"""Core Executor adapter for task execution."""

from __future__ import annotations

from collections.abc import Mapping
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
    """Runs tasks through a handler or intent-routed handlers and returns Core Observations."""

    handler: TaskHandler | None = None
    handlers: Mapping[str, TaskHandler] | None = None

    def execute(self, step: PlanStep, context: ExecutionContext) -> Observation:
        task_input = TaskInput.from_step(step, context)
        task = Task.create(task_input)
        task.start()

        handler = self._resolve_handler(task_input)
        if handler is None:
            err_msg = f"no handler registered for capability intent: '{task_input.intent}'"
            task.fail(err_msg)
            return Observation(
                step_id=task_input.step_id,
                content=err_msg,
                success=False,
                metadata={"error": "unregistered_intent"},
            )

        try:
            output = handler.run(task_input, context)
        except TaskError as exc:
            task.fail(str(exc))
            return Observation(step_id=task_input.step_id, content=str(exc), success=False)
        except Exception as exc:
            task.fail(str(exc))
            return Observation(step_id=task_input.step_id, content=str(exc), success=False)

        if output.success:
            task.complete(output)
        else:
            task.fail(output.content or "task failed")
            task.output = output

        return _to_observation(task_input.step_id, output)

    def _resolve_handler(self, task_input: TaskInput) -> TaskHandler | None:
        if self.handlers:
            intent = (
                task_input.step_metadata.get("action")
                or task_input.intent
                or ""
            ).strip().lower()

            meta = task_input.step_metadata
            is_github = (
                meta.get("capability") == "github"
                or "owner" in meta
                or (isinstance(meta.get("arguments"), dict) and "owner" in meta["arguments"])
                or (isinstance(meta.get("args"), dict) and "owner" in meta["args"])
                or (isinstance(meta.get("params"), dict) and "owner" in meta["params"])
                or (isinstance(meta.get("parameters"), dict) and "owner" in meta["parameters"])
                or (isinstance(meta.get("repo"), str) and "/" in meta["repo"])
                or (isinstance(meta.get("repository"), str) and "/" in meta["repository"])
            )
            if is_github:
                gh_intent = f"github_{intent}"
                if gh_intent in self.handlers:
                    return self.handlers[gh_intent]

            if intent in self.handlers:
                return self.handlers[intent]

            for key, h in self.handlers.items():
                if key.strip().lower() == intent:
                    return h

            return self.handler

        return self.handler

    def register_handler(self, intent: str, handler: TaskHandler) -> None:
        """Register a handler for a specific intent."""
        if self.handlers is None:
            self.handlers = {}
        elif not isinstance(self.handlers, dict):
            self.handlers = dict(self.handlers)
        self.handlers[intent.strip().lower()] = handler
