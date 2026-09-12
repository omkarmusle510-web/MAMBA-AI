"""Web search provider abstraction and Tavily implementation for Mamba."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol
import urllib.error
import urllib.parse
import urllib.request

from .errors import (
    WebAuthenticationError,
    WebProviderError,
    WebRateLimitError,
    WebSearchUnavailableError,
)
from .types import SearchResultItem, WebSearchResult

_DEFAULT_TAVILY_URL = "https://api.tavily.com/search"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_ENV_KEY = "TAVILY_API_KEY"


def _sanitize(text: str, key: str | None = None) -> str:
    """Ensure no API key or token string appears in error messages."""
    if not text:
        return ""
    clean = str(text)
    if key and key in clean:
        clean = clean.replace(key, "[REDACTED_API_KEY]")
    env_key = os.environ.get(_ENV_KEY, "").strip()
    if env_key and env_key in clean:
        clean = clean.replace(env_key, "[REDACTED_API_KEY]")
    return clean


class WebSearchProvider(Protocol):
    """Protocol for web search providers."""

    def search(self, query: str, *, max_results: int = 5) -> WebSearchResult:
        """Execute a single search query and return structured results."""
        ...


class TavilyProvider:
    """Concrete WebSearchProvider backed by the Tavily Search API.

    Adheres strictly to the single-request rule: exactly one HTTP request
    per search() call. No retries, recursive crawling, or query expansion.
    Reads TAVILY_API_KEY from the environment only. Never loads .env itself.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = _DEFAULT_TAVILY_URL,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        _http_post: Any = None,
    ) -> None:
        self._explicit_key = api_key
        self._base_url = base_url
        self._timeout = float(timeout)
        self._http_post = _http_post or self._default_http_post

    def _resolve_api_key(self) -> str:
        """Resolve API key strictly from explicit param or process environment."""
        key = self._explicit_key or os.environ.get(_ENV_KEY, "")
        key = key.strip().strip('"').strip("'")
        if not key:
            raise WebSearchUnavailableError(
                "Tavily web search is unconfigured: TAVILY_API_KEY environment "
                "variable is missing or empty. Please set TAVILY_API_KEY in the environment."
            )
        return key

    def _default_http_post(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Perform a single HTTP POST request to Tavily API."""
        key = payload.get("api_key", "")
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mamba-AI-WebSearch",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                body = ""

            clean_body = _sanitize(body, key)
            if exc.code in (401, 403):
                raise WebAuthenticationError(
                    f"Tavily authentication failed (HTTP {exc.code}): {clean_body or exc.reason}"
                ) from exc
            if exc.code == 429:
                raise WebRateLimitError(
                    f"Tavily rate limit exceeded (HTTP 429): {clean_body or exc.reason}"
                ) from exc
            raise WebProviderError(
                f"Tavily API HTTP {exc.code}: {clean_body or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise WebProviderError(
                f"Tavily network connection failed: {_sanitize(str(exc.reason), key)}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise WebProviderError(
                f"Tavily returned invalid JSON response: {exc}"
            ) from exc
        except Exception as exc:
            raise WebProviderError(
                f"Tavily request failed: {_sanitize(str(exc), key)}"
            ) from exc

    def search(self, query: str, *, max_results: int = 5) -> WebSearchResult:
        """Execute a single search query against Tavily API."""
        key = self._resolve_api_key()

        clean_query = query.strip()
        payload = {
            "api_key": key,
            "query": clean_query,
            "max_results": max(1, min(max_results, 10)),
            "search_depth": "basic",
            "include_answer": False,
        }

        raw_response = self._http_post(self._base_url, payload, self._timeout)

        raw_results = raw_response.get("results")
        if not isinstance(raw_results, list):
            return WebSearchResult(query=clean_query, results=())

        items: list[SearchResultItem] = []
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            url = str(entry.get("url") or "").strip()
            content = str(entry.get("content") or "").strip()
            score = entry.get("score")
            score_val = float(score) if isinstance(score, (int, float)) else None

            # Extract domain from url if available
            domain: str | None = None
            if url:
                try:
                    parsed = urllib.parse.urlparse(url)
                    domain = parsed.netloc or None
                except Exception:
                    domain = None

            items.append(
                SearchResultItem(
                    title=title,
                    url=url,
                    content=content,
                    score=score_val,
                    domain=domain,
                )
            )

        resp_time = raw_response.get("response_time")
        resp_time_val = float(resp_time) if isinstance(resp_time, (int, float)) else None

        return WebSearchResult(
            query=clean_query,
            results=tuple(items),
            response_time=resp_time_val,
        )

