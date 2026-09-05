"""Mixed-capability task execution for Mamba."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tasks.executor import TaskExecutor
from tasks.protocols import TaskHandler
from tools.protocols import ToolExecutor
from tools.tool import BaseTool

from .filesystem import (
    CreateDirectorySkill,
    DeleteSkill,
    FilesystemTaskHandler,
    ListDirectorySkill,
    ReadFileSkill,
    WriteFileSkill,
)
from .terminal import TerminalSkill, TerminalTaskHandler

_FILESYSTEM_INTENTS = frozenset(
    {
        "list_directory",
        "list_dir",
        "read_file",
        "write_file",
        "create_directory",
        "create_dir",
        "mkdir",
        "make_directory",
        "delete",
        "delete_file",
        "delete_directory",
        "remove",
        "remove_file",
        "rmdir",
        "unlink",
    }
)

_TERMINAL_INTENTS = frozenset(
    {
        "execute_command",
        "run_command",
        "terminal",
        "exec",
        "command",
    }
)


def create_mixed_task_executor(
    *,
    root_dir: str | Path | None = None,
    default_timeout: float | None = None,
    tool_executor: ToolExecutor | None = None,
    filesystem_tool: BaseTool | None = None,
    terminal_tool: BaseTool | None = None,
    filesystem_handler: TaskHandler | None = None,
    terminal_handler: TaskHandler | None = None,
    extra_handlers: Mapping[str, TaskHandler] | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to both filesystem and terminal capabilities."""
    if filesystem_handler is None:
        filesystem_handler = FilesystemTaskHandler(
            list_directory_skill=ListDirectorySkill(root_dir=root_dir, executor=tool_executor),
            read_file_skill=ReadFileSkill(root_dir=root_dir, executor=tool_executor),
            write_file_skill=WriteFileSkill(root_dir=root_dir, executor=tool_executor),
            create_directory_skill=CreateDirectorySkill(root_dir=root_dir, executor=tool_executor),
            delete_skill=DeleteSkill(root_dir=root_dir, executor=tool_executor),
        )

    if terminal_handler is None:
        terminal_handler = TerminalTaskHandler(
            terminal_skill=TerminalSkill(
                tool=terminal_tool,
                root_dir=root_dir,
                default_timeout=default_timeout,
                executor=tool_executor,
            )
        )

    handlers: dict[str, TaskHandler] = {}
    for intent in _FILESYSTEM_INTENTS:
        handlers[intent] = filesystem_handler

    for intent in _TERMINAL_INTENTS:
        handlers[intent] = terminal_handler

    if extra_handlers:
        handlers.update(extra_handlers)

    return TaskExecutor(handlers=handlers)

