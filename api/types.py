"""Shared data types for the API boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class APIRequest:
    """An external request submitted to Mamba through the API boundary."""

    input: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class APIResponse:
    """The outcome returned for a handled API request."""

    success: bool
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
