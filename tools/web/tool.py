"""Web tool factories for Mamba."""

from __future__ import annotations

from tools.tool import BaseTool

from .providers import WebSearchProvider
from .search import WebSearchTool
from .types import WebSearchAction


def create_web_tools(provider: WebSearchProvider | None = None) -> dict[str, BaseTool]:
    """Create all standard web tools."""
    return {
        WebSearchAction.WEB_SEARCH.value: WebSearchTool(provider=provider),
    }

