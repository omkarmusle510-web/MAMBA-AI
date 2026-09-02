"""GitHub tool errors."""

from __future__ import annotations

from tools.errors import ToolError, ToolExecutionError, ToolValidationError


class GitHubError(ToolError):
    """Base error for all GitHub tool operations."""


class GitHubValidationError(GitHubError, ToolValidationError):
    """Raised when GitHub tool arguments or inputs are invalid."""


class GitHubAPIError(GitHubError, ToolExecutionError):
    """Raised when a GitHub REST API request fails."""


class GitHubAuthenticationError(GitHubAPIError):
    """Raised when GitHub authentication is missing, invalid, or denied (401)."""


class GitHubNotFoundError(GitHubAPIError):
    """Raised when a requested GitHub resource is not found (404)."""


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub API rate limits are exceeded (403)."""

