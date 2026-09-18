"""Mamba Core layer foundation."""

from .brain import Brain, create_brain
from .capabilities import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityStatus,
    default_capability_registry,
)
from .context import ExecutionContext
from .errors import (
    CoreError,
    ExecutionError,
    PlanningError,
    StateTransitionError,
    ValidationError,
)
from .orchestrator import Orchestrator
from .protocols import Executor, Planner
from .runtime import MambaRuntime
from .state import ExecutionRecord, ExecutionState
from .types import (
    ExecutionPlan,
    ExecutionResult,
    Observation,
    PlanStep,
    ResultStatus,
    UserRequest,
)

__all__ = [
    "Brain",
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "CapabilityStatus",
    "CoreError",
    "ExecutionContext",
    "ExecutionError",
    "ExecutionPlan",
    "ExecutionRecord",
    "ExecutionResult",
    "ExecutionState",
    "Executor",
    "MambaRuntime",
    "Observation",
    "Orchestrator",
    "PlanStep",
    "Planner",
    "PlanningError",
    "ResultStatus",
    "StateTransitionError",
    "UserRequest",
    "ValidationError",
    "create_brain",
    "default_capability_registry",
]
