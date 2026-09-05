"""Mixed-capability task execution for Mamba."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tasks.executor import TaskExecutor
from tasks.protocols import TaskHandler
from tools.github.tool import GitHubClient
from tools.github.types import GitHubCredential
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
from .github import (
    GetIssueSkill,
    GetPullRequestSkill,
    GetRepositorySkill,
    GitHubListDirectorySkill,
    GitHubReadFileSkill,
    GitHubTaskHandler,
    ListIssuesSkill,
    ListPullRequestsSkill,
    SearchCodeSkill,
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

_GITHUB_INTENTS = frozenset(
    {
        "get_repository",
        "github_get_repository",
        "get_repo",
        "github_repo",
        "github_read_file",
        "read_github_file",
        "github_list_directory",
        "list_github_directory",
        "github_list_dir",
        "get_issue",
        "github_get_issue",
        "list_issues",
        "github_list_issues",
        "get_pull_request",
        "github_get_pull_request",
        "get_pr",
        "github_get_pr",
        "list_pull_requests",
        "github_list_pull_requests",
        "list_prs",
        "github_list_prs",
        "search_code",
        "github_search_code",
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
    github_client: GitHubClient | None = None,
    github_credential: GitHubCredential | None = None,
    github_token: str | None = None,
    github_timeout: float = 30.0,
    github_handler: TaskHandler | None = None,
    extra_handlers: Mapping[str, TaskHandler] | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to filesystem, terminal, and GitHub capabilities."""
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

    if github_handler is None:
        c = github_client or GitHubClient(
            credential=github_credential,
            token=github_token,
            timeout=github_timeout,
        )
        github_handler = GitHubTaskHandler(
            get_repository_skill=GetRepositorySkill(client=c, executor=tool_executor),
            read_file_skill=GitHubReadFileSkill(client=c, executor=tool_executor),
            list_directory_skill=GitHubListDirectorySkill(client=c, executor=tool_executor),
            get_issue_skill=GetIssueSkill(client=c, executor=tool_executor),
            list_issues_skill=ListIssuesSkill(client=c, executor=tool_executor),
            get_pull_request_skill=GetPullRequestSkill(client=c, executor=tool_executor),
            list_pull_requests_skill=ListPullRequestsSkill(client=c, executor=tool_executor),
            search_code_skill=SearchCodeSkill(client=c, executor=tool_executor),
        )

    handlers: dict[str, TaskHandler] = {}
    for intent in _FILESYSTEM_INTENTS:
        handlers[intent] = filesystem_handler

    for intent in _TERMINAL_INTENTS:
        handlers[intent] = terminal_handler

    for intent in _GITHUB_INTENTS:
        handlers[intent] = github_handler

    if extra_handlers:
        handlers.update(extra_handlers)

    return TaskExecutor(handlers=handlers)

