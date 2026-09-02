"""Contracts for agent reasoning."""

from __future__ import annotations

from typing import Protocol

from .types import AgentInput, AgentOutput


class AgentHandler(Protocol):
    """Reasons about a goal and produces a planning result."""

    def reason(self, input: AgentInput) -> AgentOutput: ...
