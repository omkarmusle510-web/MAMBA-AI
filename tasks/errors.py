"""Tasks-layer exception hierarchy."""


class TaskError(Exception):
    """Base error for all Tasks-layer failures."""


class TaskExecutionError(TaskError):
    """Raised when task execution fails."""
