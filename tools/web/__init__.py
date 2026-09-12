"""Mamba Web tools layer."""

from .errors import (
    WebAuthenticationError,
    WebProviderError,
    WebRateLimitError,
    WebSearchError,
    WebSearchUnavailableError,
    WebToolError,
    WebValidationError,
)
from .providers import TavilyProvider, WebSearchProvider
from .search import WebSearchHandler, WebSearchTool
from .tool import create_web_tools
from .types import (
    WEB_OPERATIONS,
    SearchResultItem,
    WebOperationDefinition,
    WebSearchAction,
    WebSearchResult,
)

__all__ = [
    "SearchResultItem",
    "TavilyProvider",
    "WEB_OPERATIONS",
    "WebAuthenticationError",
    "WebOperationDefinition",
    "WebProviderError",
    "WebRateLimitError",
    "WebSearchAction",
    "WebSearchError",
    "WebSearchHandler",
    "WebSearchResult",
    "WebSearchTool",
    "WebSearchUnavailableError",
    "WebToolError",
    "WebValidationError",
    "WebSearchProvider",
    "create_web_tools",
]

