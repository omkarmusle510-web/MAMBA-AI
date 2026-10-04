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
        cancel_token: Any = None,
        stream_sink: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        """Execute a user request through the complete Mamba Core lifecycle.

        All three keyword arguments are optional. When none are supplied this
        delegates to ``Brain.run(request)`` exactly as before, so existing
        callers and test doubles are unaffected.
        """
        kwargs: dict[str, Any] = {}
        if on_progress is not None:
            kwargs["on_progress"] = on_progress
        if cancel_token is not None:
            kwargs["cancel_token"] = cancel_token
        if stream_sink is not None:
            kwargs["stream_sink"] = stream_sink
        return self.brain.run(request, **kwargs)

    def shutdown(self) -> None:
        """Bounded application teardown (stops MCP/browser, closes memory).

        Separate from per-request cancellation; safe to call once at shutdown.
        """
        shutdown = getattr(self.brain, "shutdown", None)
        if callable(shutdown):
            shutdown()
