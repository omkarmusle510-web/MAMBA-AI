"""Mamba Skills layer."""

from .analyze import (
    AnalyzeSkill,
    AnalyzeTaskHandler,
    create_analyze_task_executor,
)
from .desktop import (
    ClearClipboardSkill,
    CloseWindowSkill,
    DesktopTaskHandler,
    FindWindowSkill,
    FocusWindowSkill,
    GetForegroundWindowSkill,
    GetWindowTitleSkill,
    ReadClipboardSkill,
    WriteClipboardSkill,
)
from .errors import SkillError, SkillExecutionError
from .filesystem import (
    CreateDirectorySkill,
    DeleteSkill,
    FilesystemSkill,
    FilesystemTaskHandler,
    ListDirectorySkill,
    ReadFileSkill,
    WriteFileSkill,
    create_filesystem_task_executor,
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
    create_github_task_executor,
)
from .mixed import create_mixed_task_executor
from .protocols import SkillHandler
from .screen import (
    OCRSkill,
    RegionOCRSkill,
    RegionScreenshotSkill,
    ScreenTaskHandler,
    ScreenshotSkill,
)
from .skill import BaseSkill, Skill, SkillTaskHandler
from .system import (
    GpuInfoSkill,
    SystemInfoSkill,
    SystemTaskHandler,
)
from .terminal import (
    TerminalSkill,
    TerminalTaskHandler,
    create_terminal_task_executor,
)
from .types import SkillInput, SkillOutput

__all__ = [
    "AnalyzeSkill",
    "AnalyzeTaskHandler",
    "BaseSkill",
    "ClearClipboardSkill",
    "CloseWindowSkill",
    "CreateDirectorySkill",
    "DeleteSkill",
    "DesktopTaskHandler",
    "FilesystemSkill",
    "FilesystemTaskHandler",
    "FindWindowSkill",
    "FocusWindowSkill",
    "GetForegroundWindowSkill",
    "GetIssueSkill",
    "GetPullRequestSkill",
    "GetRepositorySkill",
    "GetWindowTitleSkill",
    "GitHubListDirectorySkill",
    "GitHubReadFileSkill",
    "GitHubTaskHandler",
    "GpuInfoSkill",
    "ListDirectorySkill",
    "ListIssuesSkill",
    "ListPullRequestsSkill",
    "OCRSkill",
    "ReadFileSkill",
    "ReadClipboardSkill",
    "RegionOCRSkill",
    "RegionScreenshotSkill",
    "ScreenTaskHandler",
    "ScreenshotSkill",
    "SearchCodeSkill",
    "Skill",
    "SkillError",
    "SkillExecutionError",
    "SkillHandler",
    "SkillInput",
    "SkillOutput",
    "SkillTaskHandler",
    "SystemInfoSkill",
    "SystemTaskHandler",
    "TerminalSkill",
    "TerminalTaskHandler",
    "WriteClipboardSkill",
    "WriteFileSkill",
    "create_analyze_task_executor",
    "create_filesystem_task_executor",
    "create_github_task_executor",
    "create_mixed_task_executor",
    "create_terminal_task_executor",
]
