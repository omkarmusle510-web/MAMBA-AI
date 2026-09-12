"""Web search tool handler and tool definition for Mamba."""

from __future__ import annotations

from typing import Any

from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import WebToolError, WebValidationError
from .providers import TavilyProvider, WebSearchProvider, _sanitize
from .types import WEB_OPERATIONS, SearchResultItem, WebSearchAction

_MAX_QUERY_LENGTH = 500
_FORBIDDEN_INPUT_KEYS = (
    "api_key",
    "token",
    "key",
    "secret",
    "password",
    "authorization",
    "tavily_api_key",
)


def _assert_no_credentials_in_input(input: ToolInput) -> None:
    """Ensure no credentials or secrets are passed through ToolInput."""
    for key in _FORBIDDEN_INPUT_KEYS:
        if key in input.arguments:
            raise WebValidationError(
                f"Passing credentials via tool input ('{key}') is forbidden. "
                "Web search credentials must be configured via environment (TAVILY_API_KEY)."
            )
        if key in input.metadata:
            raise WebValidationError(
                f"Passing credentials via tool metadata ('{key}') is forbidden."
            )


def _format_search_results_text(query: str, results: tuple[SearchResultItem, ...]) -> str:
    """Format structured search results into a clean, source-attributed markdown string."""
    if not results:
        return f"No web search results found for: \"{query}\""

    lines: list[str] = [f"Web search results for: \"{query}\"\n"]
    for i, item in enumerate(results, start=1):
        domain_tag = f" ({item.domain})" if item.domain else ""
        lines.append(f"{i}. [{item.title}]({item.url}){domain_tag}")
        if item.content:
            snippet = item.content.strip()
            # Bounded snippet length
            if len(snippet) > 400:
                snippet = snippet[:400].rstrip() + "..."
            lines.append(f"   {snippet}")
        lines.append("")

    return "\n".join(lines).strip()


class WebSearchHandler:
    """Handler for executing web search queries through a WebSearchProvider."""

    def __init__(self, provider: WebSearchProvider | None = None) -> None:
        self._provider = provider or TavilyProvider()

    @property
    def provider(self) -> WebSearchProvider:
        return self._provider

    def run(self, input: ToolInput) -> ToolOutput:
        try:
            _assert_no_credentials_in_input(input)
        except WebValidationError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={"action": "web_search", "validation_error": True},
            )

        query_raw = input.arguments.get("query")
        if query_raw is None:
            return ToolOutput(
                success=False,
                error="Missing required argument 'query' for web search.",
                metadata={"action": "web_search", "validation_error": True},
            )

        query = str(query_raw).strip()
        if not query:
            return ToolOutput(
                success=False,
                error="Search query must not be empty.",
                metadata={"action": "web_search", "validation_error": True},
            )

        if len(query) > _MAX_QUERY_LENGTH:
            return ToolOutput(
                success=False,
                error=f"Search query exceeds maximum length of {_MAX_QUERY_LENGTH} characters.",
                metadata={"action": "web_search", "validation_error": True},
            )

        max_results_raw = input.arguments.get("max_results", 5)
        try:
            max_results = int(max_results_raw)
        except (TypeError, ValueError):
            max_results = 5

        try:
            search_result = self._provider.search(query, max_results=max_results)
        except WebToolError as exc:
            return ToolOutput(
                success=False,
                error=_sanitize(str(exc)),
                metadata={
                    "action": "web_search",
                    "provider": "tavily",
                    "available": False,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=_sanitize(f"Unexpected search error: {exc}"),
                metadata={
                    "action": "web_search",
                    "provider": "tavily",
                    "error": "unexpected_exception",
                },
            )

        formatted_text = _format_search_results_text(search_result.query, search_result.results)
        sources_meta = [
            {"title": r.title, "url": r.url, "domain": r.domain}
            for r in search_result.results
        ]

        return ToolOutput(
            success=True,
            result=formatted_text,
            metadata={
                "action": "web_search",
                "provider": "tavily",
                "query": search_result.query,
                "result_count": len(search_result.results),
                "sources": sources_meta,
            },
        )


class WebSearchTool(BaseTool):
    """Mamba tool for web search."""

    def __init__(self, provider: WebSearchProvider | None = None) -> None:
        defn = WEB_OPERATIONS[WebSearchAction.WEB_SEARCH]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=WebSearchHandler(provider=provider),
        )

