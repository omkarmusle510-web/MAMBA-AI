"""Error types for email capabilities."""

from __future__ import annotations

from tools.errors import ToolError


class EmailError(ToolError):
    """Base error for email operations."""


class EmailProviderError(EmailError):
    """Raised when an underlying email provider fails."""


class EmailNotFoundError(EmailError):
    """Raised when a specified email or draft cannot be found."""


class EmailValidationError(EmailError):
    """Raised when email arguments or payload validation fails."""

