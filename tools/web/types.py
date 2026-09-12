"""Types and data structures for web search tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class WebSearchAction(StrEnum):
    """Supported web search operations."""

    WEB_SEARCH = "web_search"


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    """Individual result item from a web search."""

    title: str
    url: str
    content: str
    score: float | None = None
    domain: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "content": self.content,
        }
        if self.score is not None:
            d["score"] = self.score
        if self.domain is not None:
            d["domain"] = self.domain
        return d


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Structured collection of search results from a search query."""

    query: str
    results: tuple[SearchResultItem, ...] = field(default_factory=tuple)
    response_time: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
        }
        if self.response_time is not None:
            d["response_time"] = self.response_time
        return d


@dataclass(frozen=True, slots=True)
class WebOperationDefinition:
    """Metadata definition for a web operation."""

    name: str
    description: str
    action: WebSearchAction = WebSearchAction.WEB_SEARCH
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False
    externally_visible: bool = True

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
            "externally_visible": self.externally_visible,
        }


WEB_OPERATIONS: dict[WebSearchAction, WebOperationDefinition] = {
    WebSearchAction.WEB_SEARCH: WebOperationDefinition(
        name=WebSearchAction.WEB_SEARCH.value,
        description="Search the web for current information dynamically using a search provider.",
        action=WebSearchAction.WEB_SEARCH,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=True,
    ),
}
