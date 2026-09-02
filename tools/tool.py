"""Tool definition and execution."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ToolExecutionError, ToolValidationError
from .protocols import ToolHandler
from .types import Tool, ToolInput, ToolOutput


class BaseTool:
    """Executable tool that delegates to an injected handler."""

    def __init__(self, tool: Tool, handler: ToolHandler) -> None:
        self._tool = tool
        self._handler = handler

    @property
    def tool(self) -> Tool:
        return self._tool

    def run(self, input: ToolInput) -> ToolOutput:
        self._validate_input(input)
        try:
            output = self._handler.run(input)
        except ToolValidationError:
            raise
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(str(exc)) from exc

        if not isinstance(output, ToolOutput):
            raise ToolExecutionError("handler returned invalid output")
        return output

    def _validate_input(self, input: ToolInput) -> None:
        if not isinstance(input.arguments, dict):
            raise ToolValidationError("arguments must be a mapping")
        if not isinstance(input.metadata, dict):
            raise ToolValidationError("metadata must be a mapping")


@dataclass(slots=True)
class StandardToolExecutor:
    """Executes a BaseTool through its handler."""

    def execute(self, tool: Tool, input: ToolInput) -> ToolOutput:
        if not isinstance(tool, BaseTool):
            raise ToolValidationError("executable tool must be a BaseTool instance")
        return tool.run(input)
