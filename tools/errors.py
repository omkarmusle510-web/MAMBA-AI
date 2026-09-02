"""Tools-layer exception hierarchy."""


class ToolError(Exception):
    """Base error for all Tools-layer failures."""


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""


class ToolValidationError(ToolError):
    """Raised when tool input or invocation is invalid."""
