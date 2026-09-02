"""Contracts for permission evaluation."""

from __future__ import annotations

from typing import Protocol

from .types import PermissionRequest, PermissionResult


class PermissionPolicy(Protocol):
    """Evaluates whether an action may proceed."""

    def evaluate(self, request: PermissionRequest) -> PermissionResult: ...
