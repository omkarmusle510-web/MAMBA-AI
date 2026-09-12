"""Errors for web tools."""

from __future__ import annotations

from tools.errors import ToolError


class WebToolError(ToolError):
    """Base exception for web tool errors."""


class WebSearchError(WebToolError):
    """General web search failure."""


class WebProviderError(WebToolError):
    """Error raised when communication with an external web search provider fails."""


class WebAuthenticationError(WebToolError):
    """Error raised when web search authentication credentials are invalid or missing."""


class WebRateLimitError(WebToolError):
    """Error raised when a web search rate limit or quota is exceeded."""


class WebValidationError(WebToolError):
    """Error raised when search query or parameters are invalid."""


class WebSearchUnavailableError(WebToolError):
    """Error raised when the web search capability is unavailable or unconfigured."""

