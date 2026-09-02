"""Contracts for verification."""

from __future__ import annotations

from typing import Protocol

from .types import VerificationRequest, VerificationResult


class Verifier(Protocol):
    """Evaluates whether an outcome matches expectations."""

    def verify(self, request: VerificationRequest) -> VerificationResult: ...
