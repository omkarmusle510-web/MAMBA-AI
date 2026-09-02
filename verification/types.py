"""Verification request and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

UNAVAILABLE: Final = object()
"""Sentinel indicating expected or actual evidence is unavailable."""


class VerificationStatus(StrEnum):
    """Outcome of a verification check."""

    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """A request to verify whether an outcome matches expectations."""

    expected: Any
    actual: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """The outcome of a verification check."""

    status: VerificationStatus
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
