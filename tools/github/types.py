"""Types and data structures for GitHub tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import os
from typing import Any


class GitHubAction(StrEnum):
    """Supported GitHub operations."""

    GET_REPOSITORY = "get_repository"
    READ_FILE = "read_file"
    LIST_DIRECTORY = "list_directory"
    GET_ISSUE = "get_issue"
    LIST_ISSUES = "list_issues"
    GET_PULL_REQUEST = "get_pull_request"
    LIST_PULL_REQUESTS = "list_pull_requests"
    SEARCH_CODE = "search_code"
    CHECK_CONNECTION = "check_connection"
    CONNECT = "connect"
    DISCONNECT = "disconnect"


class GitHubCredential:
    """Lightweight credential provider for GitHub."""

    def __init__(self, token: str | None = None) -> None:
        self._token = token.strip() if token and token.strip() else None

    def get_token(self) -> str | None:
        if self._token:
            return self._token
        env_token = os.environ.get("GITHUB_TOKEN")
        if env_token and env_token.strip():
            return env_token.strip()
        return None

    def is_configured(self) -> bool:
        return self.get_token() is not None

    def set_token(self, token: str | None) -> None:
        self._token = token.strip() if token and token.strip() else None

    def __repr__(self) -> str:
        return f"GitHubCredential(configured={self.is_configured()})"


@dataclass(frozen=True, slots=True)
class GitHubOperationDefinition:
    """Metadata definition for a GitHub operation."""

    action: GitHubAction
    description: str
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False
    risk_level: str = "low"

    def to_metadata(self) -> dict[str, Any]:
        """Convert definition to metadata dictionary."""
        return {
            "action": self.action.value,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
            "risk_level": self.risk_level,
        }


GITHUB_OPERATIONS: dict[GitHubAction, GitHubOperationDefinition] = {
    GitHubAction.GET_REPOSITORY: GitHubOperationDefinition(
        action=GitHubAction.GET_REPOSITORY,
        description="Retrieve metadata for a GitHub repository.",
        user_sensitive=False,
    ),
    GitHubAction.READ_FILE: GitHubOperationDefinition(
        action=GitHubAction.READ_FILE,
        description="Read the text content of a file from a GitHub repository.",
        user_sensitive=False,
    ),
    GitHubAction.LIST_DIRECTORY: GitHubOperationDefinition(
        action=GitHubAction.LIST_DIRECTORY,
        description="List immediate entries in a directory in a GitHub repository.",
        user_sensitive=False,
    ),
    GitHubAction.GET_ISSUE: GitHubOperationDefinition(
        action=GitHubAction.GET_ISSUE,
        description="Retrieve details for an issue in a GitHub repository.",
        user_sensitive=False,
    ),
    GitHubAction.LIST_ISSUES: GitHubOperationDefinition(
        action=GitHubAction.LIST_ISSUES,
        description="List issues in a GitHub repository with optional state and limit.",
        user_sensitive=False,
    ),
    GitHubAction.GET_PULL_REQUEST: GitHubOperationDefinition(
        action=GitHubAction.GET_PULL_REQUEST,
        description="Retrieve details for a pull request in a GitHub repository.",
        user_sensitive=False,
    ),
    GitHubAction.LIST_PULL_REQUESTS: GitHubOperationDefinition(
        action=GitHubAction.LIST_PULL_REQUESTS,
        description="List pull requests in a GitHub repository with optional state and limit.",
        user_sensitive=False,
    ),
    GitHubAction.SEARCH_CODE: GitHubOperationDefinition(
        action=GitHubAction.SEARCH_CODE,
        description="Search code strictly within a specified GitHub repository.",
        user_sensitive=False,
    ),
    GitHubAction.CHECK_CONNECTION: GitHubOperationDefinition(
        action=GitHubAction.CHECK_CONNECTION,
        description="Check GitHub connection status using the authenticated user endpoint.",
        user_sensitive=False,
    ),
    GitHubAction.CONNECT: GitHubOperationDefinition(
        action=GitHubAction.CONNECT,
        description="Validate and connect GitHub account using configured credentials.",
        user_sensitive=True,
    ),
    GitHubAction.DISCONNECT: GitHubOperationDefinition(
        action=GitHubAction.DISCONNECT,
        description="Disconnect GitHub account or report environment configuration status.",
        user_sensitive=True,
    ),
}


@dataclass(frozen=True, slots=True)
class GitHubConfig:
    """Configuration for GitHub tools."""

    token: str | None = None
    timeout: float = 30.0
    api_url: str = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class GitHubConnectionStatus:
    """Status of GitHub user connection."""

    connected: bool
    username: str | None = None
    name: str | None = None
    html_url: str | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "connected": self.connected,
            "username": self.username,
            "name": self.name,
            "html_url": self.html_url,
        }
        if self.error is not None:
            d["error"] = self.error
        if self.message is not None:
            d["message"] = self.message
        return d


@dataclass(frozen=True, slots=True)
class RepositoryMetadata:
    """Metadata describing a GitHub repository."""

    owner: str
    repo: str
    full_name: str
    description: str | None = None
    private: bool = False
    fork: bool = False
    html_url: str | None = None
    default_branch: str = "main"
    stargazers_count: int = 0
    forks_count: int = 0
    open_issues_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "full_name": self.full_name,
            "description": self.description,
            "private": self.private,
            "fork": self.fork,
            "html_url": self.html_url,
            "default_branch": self.default_branch,
            "stargazers_count": self.stargazers_count,
            "forks_count": self.forks_count,
            "open_issues_count": self.open_issues_count,
        }


@dataclass(frozen=True, slots=True)
class FileContent:
    """Content of a file from a GitHub repository."""

    owner: str
    repo: str
    path: str
    content: str
    size: int
    sha: str | None = None
    ref: str | None = None
    html_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "path": self.path,
            "ref": self.ref,
            "content": self.content,
            "size": self.size,
            "sha": self.sha,
            "html_url": self.html_url,
        }


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """An entry in a directory within a GitHub repository."""

    name: str
    path: str
    type: str
    size: int | None = None
    sha: str | None = None
    html_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "size": self.size,
            "sha": self.sha,
            "html_url": self.html_url,
        }


@dataclass(frozen=True, slots=True)
class IssueData:
    """Details of an issue in a GitHub repository."""

    owner: str
    repo: str
    number: int
    title: str
    state: str
    html_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    author: str | None = None
    comments_count: int = 0
    is_pull_request: bool = False
    body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "html_url": self.html_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "author": self.author,
            "comments_count": self.comments_count,
            "is_pull_request": self.is_pull_request,
            "body": self.body,
        }


@dataclass(frozen=True, slots=True)
class PullRequestData:
    """Details of a pull request in a GitHub repository."""

    owner: str
    repo: str
    number: int
    title: str
    state: str
    html_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    merged_at: str | None = None
    author: str | None = None
    head_ref: str | None = None
    base_ref: str | None = None
    draft: bool = False
    merged: bool = False
    body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "html_url": self.html_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "merged_at": self.merged_at,
            "author": self.author,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "draft": self.draft,
            "merged": self.merged,
            "body": self.body,
        }


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Results of a repository-scoped code search."""

    owner: str
    repo: str
    query: str
    total_count: int
    items: Sequence[dict[str, Any]] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "query": self.query,
            "total_count": self.total_count,
            "items": list(self.items),
        }
