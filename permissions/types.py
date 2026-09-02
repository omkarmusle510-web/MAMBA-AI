"""Permission request and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PermissionDecision(StrEnum):
    """Whether an action may proceed."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class RiskLevel(StrEnum):
    """Data-driven risk classification for an action."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """A request to evaluate whether an action may proceed."""

    action: str
    tool_name: str
    risk_level: RiskLevel
    resource: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ValueError("action must not be empty")
        if not self.tool_name.strip():
            raise ValueError("tool_name must not be empty")


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """The outcome of a permission evaluation."""

    decision: PermissionDecision
    reason: str
    request: PermissionRequest
    metadata: dict[str, Any] = field(default_factory=dict)
