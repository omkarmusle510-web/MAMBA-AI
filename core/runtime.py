"""Public Core entry point — canonical application-facing runtime boundary.

All user-facing interfaces (text CLI, voice, future API) converge
through MambaRuntime into Brain.run(), which coordinates the full
Mamba Core lifecycle:

    Request → Context → Memory → Planning → Model Routing
    → Permission → Execution → Observation → Verification
    → Memory Update → Response
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .brain import Brain
from .types import ExecutionResult, UserRequest


@dataclass(slots=True)
class MambaRuntime:
    """Application-facing runtime boundary for Mamba.

    Flow:
        Text input ─┐
                    ├→ MambaRuntime.run() → Brain.run() → ExecutionResult
        Voice input ┘
    """

    brain: Brain

    def run(
        self,
        request: str | UserRequest,
        *,
        on_progress: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        """Execute a user request through the complete Mamba Core lifecycle."""
        if on_progress is not None:
            return self.brain.run(request, on_progress=on_progress)
        return self.brain.run(request)
