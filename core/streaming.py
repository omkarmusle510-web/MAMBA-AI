"""Ambient model-response stream sink.

Mirrors the cooperative-cancellation design in :mod:`core.cancellation`:
``Brain.run`` binds a sink for the duration of one execution on its single
worker thread, so deep collaborators that cannot receive it via parameters
— notably ``AnalyzeSkill``'s answer model call — can push incremental text
without invasive signature changes.

A sink is any ``Callable[[str], None]`` receiving ordered text deltas. It is
presentation-only and provisional: it never replaces the authoritative
complete ``ModelResponse`` that flows into observations, verification,
memory, and the final ``ExecutionResult``.

This module is a dependency-free leaf: it imports only the standard library
so lower layers can reference it without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any

_current_sink: ContextVar[Callable[[str], None] | None] = ContextVar(
    "mamba_stream_sink", default=None
)


def set_current_sink(sink: Callable[[str], None] | None) -> Token[Any]:
    """Bind ``sink`` as the ambient stream sink for this context."""
    return _current_sink.set(sink)


def reset_current_sink(reset_token: Token[Any]) -> None:
    """Restore the previous sink. Best-effort across contexts."""
    try:
        _current_sink.reset(reset_token)
    except (ValueError, LookupError):
        _current_sink.set(None)


def current_sink() -> Callable[[str], None] | None:
    """The ambient sink, or ``None`` when nothing is streaming."""
    return _current_sink.get()


def emit_token(text: str) -> bool:
    """Push ``text`` to the bound sink. Returns True if a sink received it."""
    sink = _current_sink.get()
    if sink is None:
        return False
    sink(text)
    return True


__all__ = [
    "current_sink",
    "emit_token",
    "reset_current_sink",
    "set_current_sink",
]
