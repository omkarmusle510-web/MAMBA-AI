"""Cooperative cancellation primitives for runtime execution.

Cancellation in Mamba is *cooperative*: a :class:`CancellationToken` is
checked at safe boundaries of the execution loop and is used to interrupt
interruptible waits (for example a pending MCP stdio response). It never
forcibly interrupts an in-flight blocking call — those remain bounded by
their own timeouts.

``MambaCancelledError`` is an ``Exception`` subclass (not ``BaseException``).
Because it derives from ``Exception``, every broad ``except Exception`` that
sits on the cancellation propagation path MUST re-raise it explicitly, so a
cancelled execution is never misclassified as an ordinary failure, silently
retried, or recorded as completed.

This module is a dependency-free leaf: it imports only the standard library
so it can be referenced from lower layers (e.g. the browser MCP client) via
a function-local import without creating an import cycle.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from typing import Any


class MambaCancelledError(Exception):
    """Raised to unwind an execution that was cancelled cooperatively."""


class CancellationToken:
    """Thread-safe cooperative cancellation signal for a single execution."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def event(self) -> threading.Event:
        """The underlying event, for interruptible waits (queue.get, etc.)."""
        return self._event

    def cancel(self) -> None:
        """Signal cancellation. Idempotent and safe to call from any thread."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise MambaCancelledError("execution cancelled")


# Ambient token for the currently executing request. ``Brain.run`` binds the
# token for the duration of one execution on its (single) worker thread, so
# deep collaborators that cannot receive it via parameters — notably the MCP
# stdio client — can observe cancellation without invasive signature changes.
_current_token: ContextVar[CancellationToken | None] = ContextVar(
    "mamba_current_cancel_token", default=None
)


def set_current_token(token: CancellationToken | None) -> Token[Any]:
    """Bind ``token`` as the ambient cancellation token for this context."""
    return _current_token.set(token)


def reset_current_token(reset_token: Token[Any]) -> None:
    """Restore the previous ambient token. Best-effort across contexts."""
    try:
        _current_token.reset(reset_token)
    except (ValueError, LookupError):
        _current_token.set(None)


def current_token() -> CancellationToken | None:
    return _current_token.get()


def current_cancel_event() -> threading.Event | None:
    """The ambient cancellation event, or ``None`` when nothing is running."""
    token = _current_token.get()
    return token.event if token is not None else None


def raise_if_cancelled() -> None:
    """Raise :class:`MambaCancelledError` if the ambient token is cancelled."""
    token = _current_token.get()
    if token is not None and token.is_cancelled:
        raise MambaCancelledError("execution cancelled")


__all__ = [
    "CancellationToken",
    "MambaCancelledError",
    "current_cancel_event",
    "current_token",
    "raise_if_cancelled",
    "reset_current_token",
    "set_current_token",
]
