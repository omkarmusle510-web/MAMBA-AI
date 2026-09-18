"""Error types for messaging capabilities."""

from __future__ import annotations

from tools.errors import ToolError


class MessagingError(ToolError):
    """Base error for messaging operations."""


class MessagingProviderError(MessagingError):
    """Raised when an underlying messaging provider fails."""


class ConversationNotFoundError(MessagingError):
    """Raised when a requested conversation or thread is not found."""


class MessagingValidationError(MessagingError):
    """Raised when messaging arguments or parameters are invalid."""

