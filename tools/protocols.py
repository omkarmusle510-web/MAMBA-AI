"""Contracts for tool execution."""

from __future__ import annotations

from typing import Protocol

from .types import Tool, ToolInput, ToolOutput


class ToolHandler(Protocol):
    """Executes one tool capability."""

    def run(self, input: ToolInput) -> ToolOutput: ...


class ToolExecutor(Protocol):
    """Runs a tool through its handler."""

    def execute(self, tool: Tool, input: ToolInput) -> ToolOutput: ...
