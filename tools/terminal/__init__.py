"""Terminal tools capability for Mamba."""

from .errors import (
    TerminalError,
    TerminalExecutionError,
    TerminalTimeoutError,
    TerminalValidationError,
)
from .tool import (
    TerminalHandler,
    TerminalTool,
    create_terminal_tool,
    parse_terminal_command,
    resolve_working_directory,
)
from .types import (
    DISALLOWED_SHELL_ARGS,
    DISALLOWED_SHELL_NAMES,
    DISALLOWED_SHELL_STEMS,
    TERMINAL_TOOL_METADATA,
    TerminalCommand,
    TerminalConfig,
    TerminalResult,
    validate_non_shell_command,
)

__all__ = [
    "DISALLOWED_SHELL_ARGS",
    "DISALLOWED_SHELL_NAMES",
    "DISALLOWED_SHELL_STEMS",
    "TERMINAL_TOOL_METADATA",
    "TerminalCommand",
    "TerminalConfig",
    "TerminalError",
    "TerminalExecutionError",
    "TerminalHandler",
    "TerminalResult",
    "TerminalTimeoutError",
    "TerminalTool",
    "TerminalValidationError",
    "create_terminal_tool",
    "parse_terminal_command",
    "resolve_working_directory",
    "validate_non_shell_command",
]
