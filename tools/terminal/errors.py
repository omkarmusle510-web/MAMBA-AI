"""Terminal tool errors."""

from __future__ import annotations

from tools.errors import ToolError, ToolExecutionError, ToolValidationError


class TerminalError(ToolError):
    """Base error for all terminal tool operations."""


class TerminalValidationError(TerminalError, ToolValidationError):
    """Raised when command inputs or configuration are invalid."""


class TerminalExecutionError(TerminalError, ToolExecutionError):
    """Raised when command execution fails unexpectedly."""


class TerminalTimeoutError(TerminalExecutionError):
    """Raised when a command execution exceeds its timeout."""

