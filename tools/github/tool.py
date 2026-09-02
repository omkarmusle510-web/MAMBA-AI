"""GitHub tools implementation for Mamba."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from .types import (
    GITHUB_OPERATIONS,
    DirectoryEntry,
    FileContent,
    GitHubAction,
    GitHubConfig,
    GitHubConnectionStatus,
    GitHubCredential,
    IssueData,
    PullRequestData,
    RepositoryMetadata,
    SearchResult,
)


def _sanitize_error_message(message: str, token: str | None = None) -> str:
    """Ensure no authorization token or sensitive headers leak into error messages."""
    if not message:
        return ""
    clean = message
    if token and token in clean:
        clean = clean.replace(token, "[REDACTED_TOKEN]")
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token and env_token.strip() and env_token in clean:
        clean = clean.replace(env_token, "[REDACTED_TOKEN]")
    return clean


def _assert_no_token_in_input(input: ToolInput) -> None:
    """Ensure no credentials are passed through ToolInput arguments or metadata."""
    forbidden_keys = ("token", "github_token", "access_token", "secret", "password", "authorization")
    for key in forbidden_keys:
        if key in input.arguments:
            raise GitHubValidationError(
                f"Passing credentials via tool input ('{key}') is not permitted. "
                "GitHub credentials must be configured via environment (GITHUB_TOKEN) or client configuration."
            )
        if key in input.metadata:
            raise GitHubValidationError(
                f"Passing credentials via tool metadata ('{key}') is not permitted."
            )


class GitHubClient:
    """Lightweight HTTP client for read-only GitHub REST API calls."""

    def __init__(
        self,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        api_url: str = "https://api.github.com",
    ) -> None:
        if credential is not None:
            self._credential = credential
        else:
            self._credential = GitHubCredential(token=token)
        self._timeout = float(timeout)
        self._api_url = api_url.rstrip("/")

    @property
    def credential(self) -> GitHubCredential:
        """Access the underlying credential provider."""
        return self._credential

    def get_token(self) -> str | None:
        """Resolve effective token from credential provider."""
        return self._credential.get_token()

    def __repr__(self) -> str:
        return f"GitHubClient(api_url='{self._api_url}', credential={self._credential!r})"

    def request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a GET request against the GitHub REST API."""
        clean_endpoint = endpoint.lstrip("/")
        url = f"{self._api_url}/{clean_endpoint}"

        if params:
            filtered_params = {
                k: v for k, v in params.items() if v is not None and v != ""
            }
            if filtered_params:
                query_string = urllib.parse.urlencode(filtered_params)
                url = f"{url}?{query_string}"

        token = self.get_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Mamba-AI/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
                if not raw_body.strip():
                    return {}
                return json.loads(raw_body)
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
                err_json = json.loads(err_body) if err_body else {}
                raw_msg = err_json.get("message") or err_body or exc.reason
            except Exception:
                raw_msg = exc.reason or str(exc)

            msg = _sanitize_error_message(str(raw_msg), token)

            if exc.code == 401:
                raise GitHubAuthenticationError(
                    f"GitHub authentication failed (401): {msg}"
                ) from exc
            elif exc.code == 403:
                remaining = exc.headers.get("x-ratelimit-remaining")
                if remaining == "0" or "rate limit" in msg.lower():
                    raise GitHubRateLimitError(
                        f"GitHub API rate limit exceeded (403): {msg}"
                    ) from exc
                raise GitHubAPIError(f"GitHub access forbidden (403): {msg}") from exc
            elif exc.code == 404:
                raise GitHubNotFoundError(
                    f"GitHub resource not found (404): {msg}"
                ) from exc
            else:
                raise GitHubAPIError(
                    f"GitHub API request failed with status {exc.code}: {msg}"
                ) from exc
        except urllib.error.URLError as exc:
            msg = _sanitize_error_message(str(exc.reason), token)
            raise GitHubAPIError(
                f"Network error communicating with GitHub: {msg}"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise GitHubAPIError(
                f"GitHub request timed out after {self._timeout} seconds"
            ) from exc
        except json.JSONDecodeError as exc:
            raise GitHubAPIError(
                f"Invalid JSON received from GitHub API: {exc}"
            ) from exc


def _validate_owner(args: dict[str, Any]) -> str:
    owner = args.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        raise GitHubValidationError("owner must be a non-empty string")
    owner = owner.strip()
    if "/" in owner or "\\" in owner:
        raise GitHubValidationError("owner must not contain path separators")
    return owner


def _validate_repo(args: dict[str, Any]) -> str:
    repo = args.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise GitHubValidationError("repo must be a non-empty string")
    repo = repo.strip()
    if "/" in repo or "\\" in repo:
        raise GitHubValidationError("repo must not contain path separators")
    return repo


def _validate_path(args: dict[str, Any], required: bool = True) -> str:
    path = args.get("path")
    if path is None or path == "":
        if required:
            raise GitHubValidationError("path must be a non-empty string")
        return ""
    if not isinstance(path, str):
        raise GitHubValidationError(
            f"path must be a string, got {type(path).__name__}"
        )
    clean_path = path.strip().replace("\\", "/").strip("/")
    if ".." in Path(clean_path).parts:
        raise GitHubValidationError(f"Invalid path traversal in '{path}'")
    if required and not clean_path:
        raise GitHubValidationError("path must be a non-empty string")
    return clean_path


def _validate_number(args: dict[str, Any], field_name: str) -> int:
    val = args.get(field_name)
    if val is None:
        val = args.get("number")
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise GitHubValidationError(
            f"{field_name} must be a positive integer, got {val}"
        )
    return val


def _validate_limit(
    args: dict[str, Any],
    default: int = 30,
    max_limit: int = 100,
) -> int:
    val = args.get("limit")
    if val is None:
        return default
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise GitHubValidationError(
            f"limit must be a positive integer, got {val}"
        )
    return min(val, max_limit)


def _validate_state(args: dict[str, Any], default: str = "open") -> str:
    state = args.get("state")
    if state is None:
        return default
    if not isinstance(state, str):
        raise GitHubValidationError(
            f"state must be a string, got {type(state).__name__}"
        )
    clean_state = state.strip().lower()
    if clean_state not in {"open", "closed", "all"}:
        raise GitHubValidationError(
            f"state must be one of: 'open', 'closed', 'all'; got '{state}'"
        )
    return clean_state


def _validate_query(args: dict[str, Any]) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise GitHubValidationError("query must be a non-empty string")
    return query.strip()


class BaseGitHubHandler:
    """Base handler for GitHub operations."""

    def __init__(
        self,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = GitHubClient(
                credential=credential,
                token=token,
                timeout=timeout,
            )

    @property
    def client(self) -> GitHubClient:
        return self._client


class CheckConnectionHandler(BaseGitHubHandler):
    """Handler to check GitHub connection status using the authenticated user endpoint."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)

        token = self._client.get_token()
        if not token:
            status = GitHubConnectionStatus(
                connected=False,
                error="GitHub is not connected: GITHUB_TOKEN is not configured.",
            )
            return ToolOutput(
                success=True,
                result=status.to_dict(),
                metadata=GITHUB_OPERATIONS[GitHubAction.CHECK_CONNECTION].to_metadata(),
            )

        try:
            data = self._client.request("user")
            status = GitHubConnectionStatus(
                connected=True,
                username=data.get("login"),
                name=data.get("name"),
                html_url=data.get("html_url"),
            )
            return ToolOutput(
                success=True,
                result=status.to_dict(),
                metadata=GITHUB_OPERATIONS[GitHubAction.CHECK_CONNECTION].to_metadata(),
            )
        except (GitHubAuthenticationError, GitHubAPIError) as exc:
            status = GitHubConnectionStatus(
                connected=False,
                error="GitHub authentication failed: invalid or expired credential.",
            )
            return ToolOutput(
                success=True,
                result=status.to_dict(),
                metadata=GITHUB_OPERATIONS[GitHubAction.CHECK_CONNECTION].to_metadata(),
            )


class ConnectHandler(BaseGitHubHandler):
    """Handler to validate and connect GitHub account using configured credentials."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)

        token = self._client.get_token()
        if not token:
            status = GitHubConnectionStatus(
                connected=False,
                error="GitHub is not connected. GITHUB_TOKEN is not configured.",
            )
            return ToolOutput(
                success=False,
                error="GitHub is not connected. GITHUB_TOKEN is not configured.",
                result=status.to_dict(),
                metadata=GITHUB_OPERATIONS[GitHubAction.CONNECT].to_metadata(),
            )

        try:
            data = self._client.request("user")
            username = data.get("login", "")
            status = GitHubConnectionStatus(
                connected=True,
                username=username,
                name=data.get("name"),
                html_url=data.get("html_url"),
                message=f"Successfully connected to GitHub as @{username}." if username else "Successfully connected to GitHub.",
            )
            return ToolOutput(
                success=True,
                result=status.to_dict(),
                metadata=GITHUB_OPERATIONS[GitHubAction.CONNECT].to_metadata(),
            )
        except (GitHubAuthenticationError, GitHubAPIError):
            status = GitHubConnectionStatus(
                connected=False,
                error="GitHub authentication failed: invalid or expired credential.",
            )
            return ToolOutput(
                success=False,
                error="GitHub authentication failed: invalid or expired credential.",
                result=status.to_dict(),
                metadata=GITHUB_OPERATIONS[GitHubAction.CONNECT].to_metadata(),
            )


class DisconnectHandler(BaseGitHubHandler):
    """Handler to report disconnect status for environment-configured credentials."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)

        self._client.credential.set_token(None)

        has_env_token = bool(os.environ.get("GITHUB_TOKEN", "").strip())
        if has_env_token:
            msg = (
                "GitHub credential is environment-configured (GITHUB_TOKEN). "
                "To permanently disconnect, unset the GITHUB_TOKEN environment variable."
            )
        else:
            msg = "GitHub is disconnected."

        status = GitHubConnectionStatus(
            connected=False,
            message=msg,
        )
        return ToolOutput(
            success=True,
            result=status.to_dict(),
            metadata=GITHUB_OPERATIONS[GitHubAction.DISCONNECT].to_metadata(),
        )


class GetRepositoryHandler(BaseGitHubHandler):
    """Handler for get_repository operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)

        data = self._client.request(f"repos/{owner}/{repo}")

        metadata = RepositoryMetadata(
            owner=owner,
            repo=repo,
            full_name=data.get("full_name", f"{owner}/{repo}"),
            description=data.get("description"),
            private=data.get("private", False),
            fork=data.get("fork", False),
            html_url=data.get("html_url"),
            default_branch=data.get("default_branch", "main"),
            stargazers_count=data.get("stargazers_count", 0),
            forks_count=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
        )

        return ToolOutput(
            success=True,
            result=metadata.to_dict(),
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.GET_REPOSITORY].to_metadata(),
                "owner": owner,
                "repo": repo,
            },
        )


class ReadFileHandler(BaseGitHubHandler):
    """Handler for read_file operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        path = _validate_path(args, required=True)
        ref = args.get("ref")
        if ref is not None and not isinstance(ref, str):
            raise GitHubValidationError("ref must be a string")

        params = {"ref": ref} if ref else None
        data = self._client.request(f"repos/{owner}/{repo}/contents/{path}", params=params)

        if isinstance(data, list) or data.get("type") == "dir":
            raise GitHubValidationError(
                f"Path '{path}' is a directory, not a regular file"
            )

        raw_b64 = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64" and raw_b64:
            try:
                content_bytes = base64.b64decode(raw_b64)
                content_str = content_bytes.decode("utf-8", errors="replace")
            except Exception as exc:
                raise GitHubAPIError(
                    f"Failed to decode base64 file content: {exc}"
                ) from exc
        else:
            content_str = raw_b64

        file_content = FileContent(
            owner=owner,
            repo=repo,
            path=path,
            content=content_str,
            size=data.get("size", len(content_str)),
            sha=data.get("sha"),
            ref=ref,
            html_url=data.get("html_url"),
        )

        return ToolOutput(
            success=True,
            result=file_content.to_dict(),
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.READ_FILE].to_metadata(),
                "owner": owner,
                "repo": repo,
                "path": path,
            },
        )


class ListDirectoryHandler(BaseGitHubHandler):
    """Handler for list_directory operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        path = _validate_path(args, required=False)
        ref = args.get("ref")
        if ref is not None and not isinstance(ref, str):
            raise GitHubValidationError("ref must be a string")

        endpoint = f"repos/{owner}/{repo}/contents/{path}" if path else f"repos/{owner}/{repo}/contents"
        params = {"ref": ref} if ref else None
        data = self._client.request(endpoint, params=params)

        if isinstance(data, dict):
            if data.get("type") == "file":
                raise GitHubValidationError(
                    f"Path '{path}' is a file, not a directory"
                )
            entries_raw = [data]
        elif isinstance(data, list):
            entries_raw = data
        else:
            entries_raw = []

        entries = [
            DirectoryEntry(
                name=item.get("name", ""),
                path=item.get("path", ""),
                type=item.get("type", "unknown"),
                size=item.get("size"),
                sha=item.get("sha"),
                html_url=item.get("html_url"),
            ).to_dict()
            for item in entries_raw
        ]
        entries.sort(key=lambda x: x["name"])

        return ToolOutput(
            success=True,
            result={
                "owner": owner,
                "repo": repo,
                "path": path,
                "ref": ref,
                "entries": entries,
            },
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.LIST_DIRECTORY].to_metadata(),
                "owner": owner,
                "repo": repo,
                "path": path,
            },
        )


class GetIssueHandler(BaseGitHubHandler):
    """Handler for get_issue operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        issue_number = _validate_number(args, "issue_number")

        data = self._client.request(f"repos/{owner}/{repo}/issues/{issue_number}")

        issue_data = IssueData(
            owner=owner,
            repo=repo,
            number=data.get("number", issue_number),
            title=data.get("title", ""),
            state=data.get("state", "open"),
            html_url=data.get("html_url"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            closed_at=data.get("closed_at"),
            author=data.get("user", {}).get("login") if data.get("user") else None,
            comments_count=data.get("comments", 0),
            is_pull_request="pull_request" in data,
            body=data.get("body"),
        )

        return ToolOutput(
            success=True,
            result=issue_data.to_dict(),
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.GET_ISSUE].to_metadata(),
                "owner": owner,
                "repo": repo,
                "issue_number": issue_number,
            },
        )


class ListIssuesHandler(BaseGitHubHandler):
    """Handler for list_issues operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        state = _validate_state(args, default="open")
        limit = _validate_limit(args, default=30, max_limit=100)

        data = self._client.request(
            f"repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": limit},
        )

        items_raw = data if isinstance(data, list) else []
        items = [
            IssueData(
                owner=owner,
                repo=repo,
                number=item.get("number", 0),
                title=item.get("title", ""),
                state=item.get("state", "open"),
                html_url=item.get("html_url"),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                closed_at=item.get("closed_at"),
                author=item.get("user", {}).get("login") if item.get("user") else None,
                comments_count=item.get("comments", 0),
                is_pull_request="pull_request" in item,
                body=item.get("body"),
            ).to_dict()
            for item in items_raw
        ]

        return ToolOutput(
            success=True,
            result={
                "owner": owner,
                "repo": repo,
                "state": state,
                "count": len(items),
                "items": items,
            },
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.LIST_ISSUES].to_metadata(),
                "owner": owner,
                "repo": repo,
                "state": state,
            },
        )


class GetPullRequestHandler(BaseGitHubHandler):
    """Handler for get_pull_request operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        pull_number = _validate_number(args, "pull_number")

        data = self._client.request(f"repos/{owner}/{repo}/pulls/{pull_number}")

        pr_data = PullRequestData(
            owner=owner,
            repo=repo,
            number=data.get("number", pull_number),
            title=data.get("title", ""),
            state=data.get("state", "open"),
            html_url=data.get("html_url"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            closed_at=data.get("closed_at"),
            merged_at=data.get("merged_at"),
            author=data.get("user", {}).get("login") if data.get("user") else None,
            head_ref=data.get("head", {}).get("ref") if data.get("head") else None,
            base_ref=data.get("base", {}).get("ref") if data.get("base") else None,
            draft=data.get("draft", False),
            merged=data.get("merged", False),
            body=data.get("body"),
        )

        return ToolOutput(
            success=True,
            result=pr_data.to_dict(),
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.GET_PULL_REQUEST].to_metadata(),
                "owner": owner,
                "repo": repo,
                "pull_number": pull_number,
            },
        )


class ListPullRequestsHandler(BaseGitHubHandler):
    """Handler for list_pull_requests operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        state = _validate_state(args, default="open")
        limit = _validate_limit(args, default=30, max_limit=100)

        data = self._client.request(
            f"repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": limit},
        )

        items_raw = data if isinstance(data, list) else []
        items = [
            PullRequestData(
                owner=owner,
                repo=repo,
                number=item.get("number", 0),
                title=item.get("title", ""),
                state=item.get("state", "open"),
                html_url=item.get("html_url"),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                closed_at=item.get("closed_at"),
                merged_at=item.get("merged_at"),
                author=item.get("user", {}).get("login") if item.get("user") else None,
                head_ref=item.get("head", {}).get("ref") if item.get("head") else None,
                base_ref=item.get("base", {}).get("ref") if item.get("base") else None,
                draft=item.get("draft", False),
                merged=item.get("merged", False),
                body=item.get("body"),
            ).to_dict()
            for item in items_raw
        ]

        return ToolOutput(
            success=True,
            result={
                "owner": owner,
                "repo": repo,
                "state": state,
                "count": len(items),
                "items": items,
            },
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.LIST_PULL_REQUESTS].to_metadata(),
                "owner": owner,
                "repo": repo,
                "state": state,
            },
        )


class SearchCodeHandler(BaseGitHubHandler):
    """Handler for search_code operation strictly scoped to a repository."""

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        args = input.arguments
        owner = _validate_owner(args)
        repo = _validate_repo(args)
        query = _validate_query(args)
        limit = _validate_limit(args, default=30, max_limit=100)

        # Enforce repo scoping
        scoped_query = f"{query} repo:{owner}/{repo}"
        data = self._client.request("search/code", params={"q": scoped_query, "per_page": limit})

        total_count = data.get("total_count", 0)
        items_raw = data.get("items", [])
        items = [
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "sha": item.get("sha"),
                "html_url": item.get("html_url"),
                "repository": f"{owner}/{repo}",
            }
            for item in items_raw
        ]

        search_result = SearchResult(
            owner=owner,
            repo=repo,
            query=query,
            total_count=total_count,
            items=tuple(items),
        )

        return ToolOutput(
            success=True,
            result=search_result.to_dict(),
            metadata={
                **GITHUB_OPERATIONS[GitHubAction.SEARCH_CODE].to_metadata(),
                "owner": owner,
                "repo": repo,
                "query": query,
            },
        )


class GitHubHandler(BaseGitHubHandler):
    """Unified handler dispatching to GitHub operations."""

    def __init__(
        self,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(client=client, credential=credential, token=token, timeout=timeout)
        self._handlers: dict[str, BaseGitHubHandler] = {
            GitHubAction.GET_REPOSITORY.value: GetRepositoryHandler(client=self._client),
            GitHubAction.READ_FILE.value: ReadFileHandler(client=self._client),
            GitHubAction.LIST_DIRECTORY.value: ListDirectoryHandler(client=self._client),
            GitHubAction.GET_ISSUE.value: GetIssueHandler(client=self._client),
            GitHubAction.LIST_ISSUES.value: ListIssuesHandler(client=self._client),
            GitHubAction.GET_PULL_REQUEST.value: GetPullRequestHandler(client=self._client),
            GitHubAction.LIST_PULL_REQUESTS.value: ListPullRequestsHandler(client=self._client),
            GitHubAction.SEARCH_CODE.value: SearchCodeHandler(client=self._client),
            GitHubAction.CHECK_CONNECTION.value: CheckConnectionHandler(client=self._client),
            GitHubAction.CONNECT.value: ConnectHandler(client=self._client),
            GitHubAction.DISCONNECT.value: DisconnectHandler(client=self._client),
        }

    def run(self, input: ToolInput) -> ToolOutput:
        _assert_no_token_in_input(input)
        if not isinstance(input.arguments, dict):
            raise GitHubValidationError("arguments must be a dictionary")

        action = (
            input.arguments.get("action")
            or input.metadata.get("action")
            or input.arguments.get("operation")
        )
        if not action:
            raise GitHubValidationError(
                "Missing required action or operation in arguments or metadata"
            )
        if not isinstance(action, str):
            raise GitHubValidationError(
                f"action must be a string, got {type(action).__name__}"
            )

        handler = self._handlers.get(action)
        if handler is None:
            raise GitHubValidationError(f"Unsupported GitHub action: '{action}'")

        return handler.run(input)


class CheckConnectionTool(BaseTool):
    """Tool to check GitHub connection status."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 30.0,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.CHECK_CONNECTION]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = CheckConnectionHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class ConnectTool(BaseTool):
    """Tool to validate and connect GitHub account."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 30.0,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.CONNECT]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = ConnectHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class DisconnectTool(BaseTool):
    """Tool to disconnect GitHub account."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 30.0,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
    ) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.DISCONNECT]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = DisconnectHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class GetRepositoryTool(BaseTool):
    """Tool to inspect a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_REPOSITORY]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = GetRepositoryHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class ReadFileTool(BaseTool):
    """Tool to read a file from a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.READ_FILE]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = ReadFileHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class ListDirectoryTool(BaseTool):
    """Tool to list entries in a directory of a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_DIRECTORY]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = ListDirectoryHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class GetIssueTool(BaseTool):
    """Tool to get an issue from a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_ISSUE]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = GetIssueHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class ListIssuesTool(BaseTool):
    """Tool to list issues from a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_ISSUES]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = ListIssuesHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class GetPullRequestTool(BaseTool):
    """Tool to get a pull request from a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.GET_PULL_REQUEST]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = GetPullRequestHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class ListPullRequestsTool(BaseTool):
    """Tool to list pull requests from a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.LIST_PULL_REQUESTS]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = ListPullRequestsHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class SearchCodeTool(BaseTool):
    """Tool to search code in a GitHub repository."""

    def __init__(self, token: str | None = None, timeout: float = 30.0, client: GitHubClient | None = None, credential: GitHubCredential | None = None) -> None:
        defn = GITHUB_OPERATIONS[GitHubAction.SEARCH_CODE]
        tool = Tool(name=defn.action.value, description=defn.description, metadata=defn.to_metadata())
        handler = SearchCodeHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


class GitHubTool(BaseTool):
    """Unified tool dispatching to all GitHub operations."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 30.0,
        client: GitHubClient | None = None,
        credential: GitHubCredential | None = None,
    ) -> None:
        tool = Tool(
            name="github",
            description="Perform GitHub operations (get_repository, read_file, list_directory, get_issue, list_issues, get_pull_request, list_pull_requests, search_code, check_connection, connect, disconnect).",
            metadata={"actions": [a.value for a in GitHubAction], "destructive": False, "risk_level": "low"},
        )
        handler = GitHubHandler(client=client, credential=credential, token=token, timeout=timeout)
        super().__init__(tool=tool, handler=handler)


def create_github_tools(
    token: str | None = None,
    timeout: float = 30.0,
    client: GitHubClient | None = None,
    credential: GitHubCredential | None = None,
) -> dict[str, BaseTool]:
    """Create all standard GitHub tools."""
    resolved_client = client or GitHubClient(credential=credential, token=token, timeout=timeout)
    get_repo_tool = GetRepositoryTool(client=resolved_client)
    read_file_tool = ReadFileTool(client=resolved_client)
    list_dir_tool = ListDirectoryTool(client=resolved_client)
    get_issue_tool = GetIssueTool(client=resolved_client)
    list_issues_tool = ListIssuesTool(client=resolved_client)
    get_pr_tool = GetPullRequestTool(client=resolved_client)
    list_prs_tool = ListPullRequestsTool(client=resolved_client)
    search_code_tool = SearchCodeTool(client=resolved_client)
    check_conn_tool = CheckConnectionTool(client=resolved_client)
    connect_tool = ConnectTool(client=resolved_client)
    disconnect_tool = DisconnectTool(client=resolved_client)
    unified_tool = GitHubTool(client=resolved_client)

    return {
        GitHubAction.GET_REPOSITORY.value: get_repo_tool,
        GitHubAction.READ_FILE.value: read_file_tool,
        GitHubAction.LIST_DIRECTORY.value: list_dir_tool,
        GitHubAction.GET_ISSUE.value: get_issue_tool,
        GitHubAction.LIST_ISSUES.value: list_issues_tool,
        GitHubAction.GET_PULL_REQUEST.value: get_pr_tool,
        GitHubAction.LIST_PULL_REQUESTS.value: list_prs_tool,
        GitHubAction.SEARCH_CODE.value: search_code_tool,
        GitHubAction.CHECK_CONNECTION.value: check_conn_tool,
        GitHubAction.CONNECT.value: connect_tool,
        GitHubAction.DISCONNECT.value: disconnect_tool,
        f"github_{GitHubAction.GET_REPOSITORY.value}": get_repo_tool,
        f"github_{GitHubAction.READ_FILE.value}": read_file_tool,
        f"github_{GitHubAction.LIST_DIRECTORY.value}": list_dir_tool,
        f"github_{GitHubAction.GET_ISSUE.value}": get_issue_tool,
        f"github_{GitHubAction.LIST_ISSUES.value}": list_issues_tool,
        f"github_{GitHubAction.GET_PULL_REQUEST.value}": get_pr_tool,
        f"github_{GitHubAction.LIST_PULL_REQUESTS.value}": list_prs_tool,
        f"github_{GitHubAction.SEARCH_CODE.value}": search_code_tool,
        f"github_{GitHubAction.CHECK_CONNECTION.value}": check_conn_tool,
        f"github_{GitHubAction.CONNECT.value}": connect_tool,
        f"github_{GitHubAction.DISCONNECT.value}": disconnect_tool,
        "github": unified_tool,
    }


def create_github_tool(
    token: str | None = None,
    timeout: float = 30.0,
    client: GitHubClient | None = None,
    credential: GitHubCredential | None = None,
) -> GitHubTool:
    """Create a unified GitHubTool instance."""
    return GitHubTool(token=token, timeout=timeout, client=client, credential=credential)
