"""Task lifecycle and state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import TaskExecutionError
from .types import TaskInput, TaskOutput


class TaskState(StrEnum):
    """Lifecycle state of one executable unit of work."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


_TERMINAL_STATES = frozenset({TaskState.COMPLETED, TaskState.FAILED})

_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING}),
    TaskState.RUNNING: frozenset({TaskState.COMPLETED, TaskState.FAILED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
}


@dataclass(slots=True)
class Task:
    """One executable unit of work with explicit lifecycle state."""

    input: TaskInput
    state: TaskState = TaskState.PENDING
    output: TaskOutput | None = None
    error: str | None = None

    @classmethod
    def create(cls, input: TaskInput) -> Task:
        return cls(input=input)

    def start(self) -> None:
        self._transition_to(TaskState.RUNNING)

    def complete(self, output: TaskOutput) -> None:
        if not output.success:
            self.fail(output.content or "task failed")
            self.output = output
            return
        self.output = output
        self._transition_to(TaskState.COMPLETED)

    def fail(self, error: str) -> None:
        self.error = error
        self._transition_to(TaskState.FAILED)

    def _transition_to(self, new_state: TaskState) -> None:
        allowed = _ALLOWED_TRANSITIONS[self.state]
        if new_state not in allowed:
            raise TaskExecutionError(
                f"cannot transition task from {self.state.value} to {new_state.value}"
            )
        self.state = new_state
