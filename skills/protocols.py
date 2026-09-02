"""Contracts for skill execution."""

from __future__ import annotations

from typing import Protocol

from .types import SkillInput, SkillOutput


class SkillHandler(Protocol):
    """Executes one reusable Mamba capability."""

    def run(self, input: SkillInput) -> SkillOutput: ...
