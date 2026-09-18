"""Mixed-capability task execution for Mamba."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from memory.protocols import MemoryStore
from memory.store import InMemoryStore
from models.protocols import ModelRouter
from tasks.executor import TaskExecutor
from tasks.protocols import TaskHandler
from tools.github.tool import GitHubClient
from tools.github.types import GitHubCredential
from tools.protocols import ToolExecutor
from tools.tool import BaseTool

from .analyze import AnalyzeSkill, AnalyzeTaskHandler
from .calendar import CalendarTaskHandler
from .desktop import DesktopTaskHandler
from .email import EmailTaskHandler
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
from .memory import MemorySkill, MemoryTaskHandler
from .messaging import MessagingTaskHandler
from .screen import ScreenTaskHandler
from .system import SystemTaskHandler
from .terminal import TerminalSkill, TerminalTaskHandler
from .web import WebTaskHandler

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

_ANALYZE_INTENTS = frozenset(
    {
        "analyze",
        "analysis",
        "calculate",
        "calculation",
        "reason",
        "reasoning",
        "compute",
        "computation",
        "evaluate",
        "eval",
        "math",
        "arithmetic",
        "clarify",
        "clarification",
        "respond",
        "response",
        "answer",
        "summarize",
        "summary",
        "explain",
        "explanation",
        "synthesize",
        "synthesis",
        "report",
        "describe",
    }
)

_MEMORY_INTENTS = frozenset(
    {
        "remember",
        "store_memory",
        "save_memory",
        "record_memory",
        "recall",
        "search_memory",
        "retrieve_memory",
        "get_memory",
        "query_memory",
        "delete_memory",
        "forget",
        "remove_memory",
        "clear_memory",
        "update_memory",
        "modify_memory",
        "summarize_memories",
        "summarize_memory",
    }
)

_DESKTOP_INTENTS = frozenset(
    {
        "get_foreground_window",
        "foreground_window",
        "active_window",
        "get_active_window",
        "get_window_title",
        "window_title",
        "find_window",
        "search_window",
        "find_windows",
        "focus_window",
        "switch_window",
        "activate_window",
        "focus",
        "close_window",
        "terminate_window",
        "kill_window",
        "destroy_window",
        "read_clipboard",
        "get_clipboard",
        "clipboard_read",
        "paste",
        "write_clipboard",
        "set_clipboard",
        "copy_to_clipboard",
        "clipboard_write",
        "copy",
        "clear_clipboard",
        "empty_clipboard",
        "clipboard_clear",
        "open_url",
        "launch_url",
        "browse",
        "open_browser",
    }
)

_SYSTEM_INTENTS = frozenset(
    {
        "system_info",
        "sys_info",
        "system_status",
        "os_info",
        "host_info",
        "specs",
        "system",
        "gpu_info",
        "nvidia_info",
        "cuda_info",
        "gpu_status",
        "gpu",
    }
)

_SCREEN_INTENTS = frozenset(
    {
        "screenshot",
        "take_screenshot",
        "capture_screen",
        "screen_capture",
        "region_screenshot",
        "capture_region",
        "region_capture",
        "ocr",
        "read_screen",
        "screen_text",
        "read_text",
        "region_ocr",
        "read_region",
        "region_text",
        "region_read",
        "visual_understanding",
        "understand_screen",
        "describe_screen",
        "look_at_screen",
        "screen_understanding",
        "interpret_screen",
    }
)

_WEB_INTENTS = frozenset({"web_search"})

_EMAIL_INTENTS = frozenset(
    {
        "search_emails",
        "search_email",
        "list_emails",
        "list_email",
        "read_email",
        "get_email",
        "summarize_email",
        "draft_email",
        "create_draft",
        "send_email",
        "reply_email",
        "reply",
        "email",
    }
)

_CALENDAR_INTENTS = frozenset(
    {
        "list_events",
        "list_calendar",
        "search_events",
        "search_calendar",
        "get_event",
        "calendar_event",
        "create_event",
        "schedule_event",
        "schedule_meeting",
        "modify_event",
        "reschedule_event",
        "reschedule_meeting",
        "cancel_event",
        "cancel_meeting",
        "delete_event",
        "check_conflicts",
        "calendar",
    }
)

_MESSAGING_INTENTS = frozenset(
    {
        "list_conversations",
        "list_messages",
        "search_conversations",
        "search_messages",
        "read_messages",
        "read_message",
        "draft_message",
        "send_message",
        "reply_message",
        "message",
        "chat",
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
    model_router: ModelRouter | None = None,
    analyze_skill: AnalyzeSkill | None = None,
    analyze_handler: TaskHandler | None = None,
    desktop_handler: TaskHandler | None = None,
    system_handler: TaskHandler | None = None,
    screen_handler: TaskHandler | None = None,
    web_handler: TaskHandler | None = None,
    memory_store: MemoryStore | None = None,
    memory_handler: TaskHandler | None = None,
    email_handler: TaskHandler | None = None,
    calendar_handler: TaskHandler | None = None,
    messaging_handler: TaskHandler | None = None,
    extra_handlers: Mapping[str, TaskHandler] | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to filesystem, terminal, GitHub, analyze, desktop, system, screen, web, memory, email, calendar, and messaging capabilities."""
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

    if analyze_handler is None:
        if analyze_skill is not None:
            analyze_handler = AnalyzeTaskHandler(analyze_skill=analyze_skill)
        elif model_router is not None:
            analyze_handler = AnalyzeTaskHandler(
                analyze_skill=AnalyzeSkill(model_router=model_router)
            )
        else:
            analyze_handler = AnalyzeTaskHandler(analyze_skill=AnalyzeSkill())

    if desktop_handler is None:
        desktop_handler = DesktopTaskHandler()

    if system_handler is None:
        system_handler = SystemTaskHandler()

    if screen_handler is None:
        screen_handler = ScreenTaskHandler(model_router=model_router)

    if web_handler is None:
        web_handler = WebTaskHandler()

    if memory_handler is None:
        store = memory_store or InMemoryStore()
        memory_handler = MemoryTaskHandler(memory_skill=MemorySkill(store=store))

    if email_handler is None:
        email_handler = EmailTaskHandler()

    if calendar_handler is None:
        calendar_handler = CalendarTaskHandler()

    if messaging_handler is None:
        messaging_handler = MessagingTaskHandler()

    handlers: dict[str, TaskHandler] = {}
    for intent in _FILESYSTEM_INTENTS:
        handlers[intent] = filesystem_handler

    for intent in _TERMINAL_INTENTS:
        handlers[intent] = terminal_handler

    for intent in _GITHUB_INTENTS:
        handlers[intent] = github_handler

    if analyze_handler is not None:
        for intent in _ANALYZE_INTENTS:
            handlers[intent] = analyze_handler

    for intent in _DESKTOP_INTENTS:
        handlers[intent] = desktop_handler

    for intent in _SYSTEM_INTENTS:
        handlers[intent] = system_handler

    for intent in _SCREEN_INTENTS:
        handlers[intent] = screen_handler

    for intent in _WEB_INTENTS:
        handlers[intent] = web_handler

    if memory_handler is not None:
        for intent in _MEMORY_INTENTS:
            handlers[intent] = memory_handler

    for intent in _EMAIL_INTENTS:
        handlers[intent] = email_handler

    for intent in _CALENDAR_INTENTS:
        handlers[intent] = calendar_handler

    for intent in _MESSAGING_INTENTS:
        handlers[intent] = messaging_handler

    if extra_handlers:
        handlers.update(extra_handlers)

    return TaskExecutor(handlers=handlers)
