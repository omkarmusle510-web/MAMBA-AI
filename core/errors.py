"""Core-layer exception hierarchy."""


class CoreError(Exception):
    """Base error for all Core-layer failures."""


class ValidationError(CoreError):
    """Raised when input data or lifecycle invariants are violated."""


class StateTransitionError(CoreError):
    """Raised when an execution state transition is not allowed."""


class PlanningError(CoreError):
    """Raised when planning fails."""


class ExecutionError(CoreError):
    """Raised when step execution fails."""
