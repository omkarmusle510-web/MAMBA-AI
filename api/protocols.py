"""Contracts for the API boundary."""

from __future__ import annotations

from typing import Protocol

from .types import APIRequest, APIResponse


class Application(Protocol):
    """Handles external requests at the application boundary."""

    def handle(self, request: APIRequest) -> APIResponse: ...
