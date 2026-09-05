"""GitHub skills capability for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tools.errors import ToolError
from tools.github.tool import (
    GetIssueTool,
    GetPullRequestTool,
    GetRepositoryTool,
    GitHubClient,
    ListDirectoryTool,
    ListIssuesTool,
    ListPullRequestsTool,
    ReadFileTool,
    SearchCodeTool,
)
from tools.github.types import (
    GITHUB_OPERATIONS,
    GitHubAction,
    GitHubCredential,
)
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_GET_REPO_SUPPORTED_INTENTS = frozenset(
    {"get_repository", "github_get_repository", "get_repo", "github_repo"}
)
_READ_FILE_SUPPORTED_INTENTS = frozenset(
    {"github_read_file", "read_github_file", "read_file"}
)
_LIST_DIR_SUPPORTED_INTENTS = frozenset(
    {"github_list_directory", "list_github_directory", "github_list_dir", "list_directory"}
)
_GET_ISSUE_SUPPORTED_INTENTS = frozenset(
    {"get_issue", "github_get_issue"}
)
_LIST_ISSUES_SUPPORTED_INTENTS = frozenset(
    {"list_issues", "github_list_issues"}
)
_GET_PR_SUPPORTED_INTENTS = frozenset(
    {"get_pull_request", "github_get_pull_request", "get_pr", "github_get_pr"}
)
_LIST_PRS_SUPPORTED_INTENTS = frozenset(
    {"list_pull_requests", "github_list_pull_requests", "list_prs", "github_list_prs"}
)
_SEARCH_CODE_SUPPORTED_INTENTS = frozenset(
    {"search_code", "github_search_code"}
)


def _extract_github_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract and normalize GitHub arguments from plan-step metadata."""
    args: dict[str, Any] = {}

    for sub_key in ("arguments", "args", "parameters", "params", "input"):
        sub = metadata.get(sub_key)
        if isinstance(sub, dict):
            args.update(sub)

    for k, v in metadata.items():
        if k not in ("arguments", "parameters", "params", "input"):
            args[k] = v

    # Extract owner / repo if formatted as repository="owner/repo" or repo="owner/repo"
    repo_val = args.get("repo") or args.get("repository")
    if isinstance(repo_val, str) and "/" in repo_val and "owner" not in args:
        parts = repo_val.strip().split("/", 1)
        args["owner"] = parts[0]
        args["repo"] = parts[1]
    elif "repo" not in args and "repository" in args:
        args["repo"] = args["repository"]

    return args


class GetRepositorySkill(BaseSkill):
    """Skill for retrieving metadata for a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_REPOSITORY]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or GetRepositoryTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_REPOSITORY]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _GET_REPO_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "get_repository failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        full_name = res.get("full_name") or f"{res.get('owner')}/{res.get('repo')}"
        desc = res.get("description") or "(no description)"
        content = (
            f"GitHub repository {full_name}:\n"
            f"- Description: {desc}\n"
            f"- Default branch: {res.get('default_branch', 'main')}\n"
            f"- Stars: {res.get('stargazers_count', 0)} | Forks: {res.get('forks_count', 0)} | "
            f"Open issues: {res.get('open_issues_count', 0)}"
        )
        return SkillOutput(
            content=content,
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class GitHubReadFileSkill(BaseSkill):
    """Skill for reading text file content from a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.READ_FILE]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or ReadFileTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.READ_FILE]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _READ_FILE_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "github read_file failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        content_str = res.get("content", "")
        return SkillOutput(
            content=content_str,
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class GitHubListDirectorySkill(BaseSkill):
    """Skill for listing entries in a directory of a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_DIRECTORY]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or ListDirectoryTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_DIRECTORY]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _LIST_DIR_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "github list_directory failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        entries = res.get("entries", [])
        path_display = res.get("path") or "/"
        owner = res.get("owner", "")
        repo = res.get("repo", "")
        lines = [f"Directory listing for '{path_display}' in {owner}/{repo}:"]
        if entries:
            for entry in entries:
                size_str = f" ({entry.get('size')} bytes)" if entry.get("size") is not None else ""
                lines.append(f"- [{entry.get('type')}] {entry.get('name')}{size_str}")
        else:
            lines.append("(empty directory)")

        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class GetIssueSkill(BaseSkill):
    """Skill for retrieving details for an issue in a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_ISSUE]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or GetIssueTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_ISSUE]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _GET_ISSUE_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        if "issue_number" not in args and "number" in args:
            args["issue_number"] = args["number"]
        elif "issue_number" not in args and "issue" in args:
            args["issue_number"] = args["issue"]

        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "get_issue failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        num = res.get("number")
        title = res.get("title", "")
        state = res.get("state", "open")
        author = res.get("author") or "unknown"
        body = res.get("body") or "(no description)"
        content = (
            f"Issue #{num} in {res.get('owner')}/{res.get('repo')}: {title} [{state}]\n"
            f"Author: @{author}\n\n"
            f"{body}"
        )
        return SkillOutput(
            content=content,
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class ListIssuesSkill(BaseSkill):
    """Skill for listing issues in a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_ISSUES]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or ListIssuesTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_ISSUES]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _LIST_ISSUES_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "list_issues failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        items = res.get("items", [])
        state = res.get("state", "open")
        owner = res.get("owner", "")
        repo = res.get("repo", "")
        lines = [f"Issues ({state}) in {owner}/{repo} (count: {len(items)}):"]
        if items:
            for item in items:
                lines.append(f"- #{item.get('number')}: {item.get('title')} (@{item.get('author') or 'unknown'}) [{item.get('state')}]")
        else:
            lines.append("(no issues found)")

        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class GetPullRequestSkill(BaseSkill):
    """Skill for retrieving details for a pull request in a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_PULL_REQUEST]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or GetPullRequestTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_PULL_REQUEST]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _GET_PR_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        if "pull_number" not in args and "number" in args:
            args["pull_number"] = args["number"]
        elif "pull_number" not in args and "pr" in args:
            args["pull_number"] = args["pr"]
        elif "pull_number" not in args and "pr_number" in args:
            args["pull_number"] = args["pr_number"]

        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "get_pull_request failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        num = res.get("number")
        title = res.get("title", "")
        state = res.get("state", "open")
        author = res.get("author") or "unknown"
        head = res.get("head_ref") or "unknown"
        base = res.get("base_ref") or "unknown"
        body = res.get("body") or "(no description)"
        content = (
            f"Pull Request #{num} in {res.get('owner')}/{res.get('repo')}: {title} [{state}]\n"
            f"Author: @{author} ({head} -> {base})\n\n"
            f"{body}"
        )
        return SkillOutput(
            content=content,
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class ListPullRequestsSkill(BaseSkill):
    """Skill for listing pull requests in a GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_PULL_REQUESTS]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or ListPullRequestsTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_PULL_REQUESTS]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _LIST_PRS_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "list_pull_requests failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        items = res.get("items", [])
        state = res.get("state", "open")
        owner = res.get("owner", "")
        repo = res.get("repo", "")
        lines = [f"Pull requests ({state}) in {owner}/{repo} (count: {len(items)}):"]
        if items:
            for item in items:
                lines.append(f"- #{item.get('number')}: {item.get('title')} (@{item.get('author') or 'unknown'}) [{item.get('state')}]")
        else:
            lines.append("(no pull requests found)")

        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


class SearchCodeSkill(BaseSkill):
    """Skill for searching code strictly within a specified GitHub repository."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.SEARCH_CODE]
        skill = Skill(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        super().__init__(skill)
        self._tool = tool or SearchCodeTool(client=client, credential=credential, token=token, timeout=timeout)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        defn = GITHUB_OPERATIONS[GitHubAction.SEARCH_CODE]
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _SEARCH_CODE_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={**defn.to_metadata(), "error": "unsupported_capability"},
            )

        args = _extract_github_metadata(input.task_input.step_metadata)
        if "query" not in args and "q" in args:
            args["query"] = args["q"]

        tool_input = ToolInput(arguments=args, metadata=dict(input.task_input.step_metadata))

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"GitHub tool execution failed: {exc}",
                success=False,
                metadata={**defn.to_metadata(), "error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "search_code failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        res = tool_output.result or {}
        items = res.get("items", [])
        total = res.get("total_count", len(items))
        owner = res.get("owner", "")
        repo = res.get("repo", "")
        query = res.get("query", "")
        lines = [f"Code search results for '{query}' in {owner}/{repo} (total: {total}):"]
        if items:
            for item in items:
                lines.append(f"- {item.get('path')} ({item.get('name')})")
        else:
            lines.append("(no matching code found)")

        return SkillOutput(
            content="\n".join(lines),
            success=True,
            metadata={**dict(tool_output.metadata), **res},
        )


@dataclass(slots=True)
class GitHubTaskHandler:
    """Dispatches GitHub task inputs to appropriate GitHub skills."""

    get_repository_skill: GetRepositorySkill
    read_file_skill: GitHubReadFileSkill
    list_directory_skill: GitHubListDirectorySkill
    get_issue_skill: GetIssueSkill
    list_issues_skill: ListIssuesSkill
    get_pull_request_skill: GetPullRequestSkill
    list_pull_requests_skill: ListPullRequestsSkill
    search_code_skill: SearchCodeSkill

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        skill_input = SkillInput.from_task(task_input, context)

        if intent in _GET_REPO_SUPPORTED_INTENTS:
            return self.get_repository_skill.run(skill_input).to_task_output()
        elif intent in _READ_FILE_SUPPORTED_INTENTS:
            return self.read_file_skill.run(skill_input).to_task_output()
        elif intent in _LIST_DIR_SUPPORTED_INTENTS:
            return self.list_directory_skill.run(skill_input).to_task_output()
        elif intent in _GET_ISSUE_SUPPORTED_INTENTS:
            return self.get_issue_skill.run(skill_input).to_task_output()
        elif intent in _LIST_ISSUES_SUPPORTED_INTENTS:
            return self.list_issues_skill.run(skill_input).to_task_output()
        elif intent in _GET_PR_SUPPORTED_INTENTS:
            return self.get_pull_request_skill.run(skill_input).to_task_output()
        elif intent in _LIST_PRS_SUPPORTED_INTENTS:
            return self.list_pull_requests_skill.run(skill_input).to_task_output()
        elif intent in _SEARCH_CODE_SUPPORTED_INTENTS:
            return self.search_code_skill.run(skill_input).to_task_output()
        else:
            return TaskOutput(
                content=f"unsupported GitHub capability intent: '{task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )


def create_github_task_executor(
    *,
    client: GitHubClient | None = None,
    credential: GitHubCredential | None = None,
    token: str | None = None,
    timeout: float = 30.0,
    executor: ToolExecutor | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to GitHub capabilities."""
    c = client or GitHubClient(credential=credential, token=token, timeout=timeout)
    handler = GitHubTaskHandler(
        get_repository_skill=GetRepositorySkill(client=c, executor=executor),
        read_file_skill=GitHubReadFileSkill(client=c, executor=executor),
        list_directory_skill=GitHubListDirectorySkill(client=c, executor=executor),
        get_issue_skill=GetIssueSkill(client=c, executor=executor),
        list_issues_skill=ListIssuesSkill(client=c, executor=executor),
        get_pull_request_skill=GetPullRequestSkill(client=c, executor=executor),
        list_pull_requests_skill=ListPullRequestsSkill(client=c, executor=executor),
        search_code_skill=SearchCodeSkill(client=c, executor=executor),
    )
    return TaskExecutor(handler=handler)

