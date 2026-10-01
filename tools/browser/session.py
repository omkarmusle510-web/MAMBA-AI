"""Browser session management and target binding for Mamba.

All browser actions go through this session object, which owns two things:

1. **Provider selection** — which browser-control adapter is in use
   (Playwright MCP, or another provider), behind one interface.
2. **Target binding** — which page an action is allowed to touch.

Target binding is the safety core. Before an action runs, the session checks
that the page it is about to touch is still the page the step was planned
against. If the current page drifted (the user navigated, a redirect landed
somewhere else, a different tab became active), the action is refused instead of
being applied to whatever happens to be in front.

Nothing here evaluates permissions or verifies outcomes: those stay in Mamba's
existing permission policy and verifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .types import (
    BrowserElement,
    BrowserElementError,
    BrowserOutcome,
    BrowserPage,
    BrowserProvider,
    BrowserTarget,
    BrowserTargetError,
)

_URL_NOISE = re.compile(r"^https?://(www\.)?", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Normalize a URL for comparison (scheme/www/case/trailing slash)."""
    value = (url or "").strip()
    if not value:
        return ""
    value = _URL_NOISE.sub("", value)
    value = value.rstrip("/")
    return value.casefold()


def same_page(left: str, right: str) -> bool:
    """Whether two URLs identify the same page for binding purposes."""
    a, b = normalize_url(left), normalize_url(right)
    if not a or not b:
        return False
    if a == b:
        return True
    # Treat a trailing fragment difference as the same document.
    return a.split("#")[0] == b.split("#")[0]


@dataclass(slots=True)
class BrowserBinding:
    """The page a step is bound to."""

    session_id: str
    tab_index: int
    url: str
    title: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tab_index": self.tab_index,
            "bound_url": self.url,
            "bound_title": self.title,
        }

    @classmethod
    def from_metadata(cls, meta: dict[str, Any]) -> "BrowserBinding | None":
        session_id = str(meta.get("session_id") or "").strip()
        if not session_id:
            return None
        tab_index = meta.get("tab_index")
        try:
            index = int(tab_index) if tab_index is not None else 0
        except (TypeError, ValueError):
            index = 0
        return cls(
            session_id=session_id,
            tab_index=index,
            url=str(meta.get("bound_url") or ""),
            title=str(meta.get("bound_title") or ""),
        )


@dataclass(slots=True)
class BrowserSession:
    """A bound browser session over one provider."""

    provider: BrowserProvider
    session_id: str = "default"
    binding: BrowserBinding | None = None
    _page: BrowserPage = field(default_factory=BrowserPage, init=False)

    # ── availability & lifecycle ──

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "provider_name", "unknown")

    def is_available(self) -> tuple[bool, str]:
        try:
            return self.provider.is_available()
        except Exception as exc:
            return False, f"browser provider unavailable: {exc}"

    def start(self) -> None:
        self.provider.start()

    def stop(self) -> None:
        self.provider.stop()

    # ── target resolution / binding ──

    def targets(self) -> list[BrowserTarget]:
        return self.provider.list_targets()

    def describe_targets(self) -> str:
        targets = self.targets()
        if not targets:
            return "No browser pages are open."
        lines = ["Open browser pages:"]
        for target in targets:
            marker = " (current)" if target.current else ""
            lines.append(
                f"- tab {target.tab_index}{marker}: {target.title or '(untitled)'} "
                f"[{target.url}]"
            )
        return "\n".join(lines)

    def bind(
        self,
        *,
        url: str = "",
        title: str = "",
        tab_index: int | None = None,
        session_id: str = "",
    ) -> BrowserBinding:
        """Bind the requested page, refusing ambiguity.

        Resolution order:

        1. a specific tab index, when the caller named one;
        2. a URL/title match against the open pages;
        3. the current page, only when the caller named nothing and the provider
           has exactly one page open.

        Any explicitly requested page that does not match an open page raises
        :class:`BrowserTargetError` — the current tab is never substituted for a
        page the caller asked for.
        """
        targets = self.targets()
        sid = session_id or self.session_id

        if tab_index is not None:
            for target in targets:
                if target.tab_index == tab_index:
                    return BrowserBinding(
                        session_id=sid,
                        tab_index=target.tab_index,
                        url=target.url,
                        title=target.title,
                    )
            raise BrowserTargetError(
                f"browser tab {tab_index} is not open; refusing to act on a different page."
            )

        if url or title:
            matches = [
                target
                for target in targets
                if (url and same_page(target.url, url))
                or (title and title.strip().casefold() in (target.title or "").casefold())
            ]
            if not matches:
                wanted = url or title
                raise BrowserTargetError(
                    f"no open browser page matches '{wanted}'; refusing to act on a "
                    "different page."
                )
            if len(matches) > 1 and url:
                exact = [target for target in matches if same_page(target.url, url)]
                matches = exact or matches
            if len(matches) > 1:
                titles = ", ".join(f"tab {t.tab_index}" for t in matches)
                raise BrowserTargetError(
                    f"multiple browser pages match '{url or title}' ({titles}); "
                    "specify which tab to use."
                )
            target = matches[0]
            return BrowserBinding(
                session_id=sid,
                tab_index=target.tab_index,
                url=target.url,
                title=target.title,
            )

        current = [target for target in targets if target.current]
        if current:
            # Exactly which page is active is known, so no ambiguity remains.
            target = current[0]
            return BrowserBinding(
                session_id=sid,
                tab_index=target.tab_index,
                url=target.url,
                title=target.title,
            )
        if len(targets) > 1:
            titles = ", ".join(f"tab {t.tab_index} ({t.title or t.url})" for t in targets)
            raise BrowserTargetError(
                "several browser pages are open and none is marked current "
                f"({titles}); say which page to use."
            )
        if not targets:
            return BrowserBinding(session_id=sid, tab_index=0, url="", title="")
        target = targets[0]
        return BrowserBinding(
            session_id=sid,
            tab_index=target.tab_index,
            url=target.url,
            title=target.title,
        )

    def attach(self, binding: BrowserBinding) -> BrowserBinding:
        """Make a previously bound page the active target, re-verifying it first."""
        targets = self.targets()
        for target in targets:
            if target.tab_index == binding.tab_index:
                if binding.url and not same_page(target.url, binding.url):
                    raise BrowserTargetError(
                        f"browser tab {binding.tab_index} is now '{target.url}', not the "
                        f"bound page '{binding.url}'; refusing to act on a different page."
                    )
                self._select_tab(binding.tab_index)
                return BrowserBinding(
                    session_id=binding.session_id,
                    tab_index=target.tab_index,
                    url=target.url,
                    title=target.title,
                )
        raise BrowserTargetError(
            f"the bound browser page (tab {binding.tab_index}) is no longer open."
        )

    def _select_tab(self, tab_index: int) -> None:
        selector = getattr(self.provider, "select_tab", None)
        if callable(selector):
            selector(tab_index)

    def select_tab(self, tab_index: int) -> None:
        """Make a specific open page the provider's active target."""
        self._select_tab(tab_index)

    def current_page_url(self) -> str:
        """The URL of the page the provider is actually on right now."""
        try:
            url, _ = self.provider.current_page()
        except Exception:
            return ""
        return url or ""

    def verify_still_bound(self, binding: BrowserBinding | None) -> BrowserBinding:
        """Re-check that the bound page is still the page we would act on.

        Two independent checks: the bound page must still be open, and the page
        the provider is currently on must still be the bound URL. A redirect, a
        user navigation, or a tab switch therefore refuses the action instead of
        applying it somewhere else.
        """
        if binding is None:
            raise BrowserTargetError("no browser page was bound before acting.")

        actual_url = self.current_page_url()
        if binding.url and actual_url and not same_page(actual_url, binding.url):
            raise BrowserTargetError(
                f"the bound browser page has changed from '{binding.url}' to "
                f"'{actual_url}'; refusing to act on a different page."
            )

        targets = self.targets()
        for target in targets:
            if target.tab_index != binding.tab_index:
                continue
            if binding.url and not same_page(target.url, binding.url):
                raise BrowserTargetError(
                    f"the bound browser page has changed from '{binding.url}' to "
                    f"'{target.url}'; refusing to act on a different page."
                )
            return BrowserBinding(
                session_id=binding.session_id,
                tab_index=target.tab_index,
                url=target.url or actual_url or binding.url,
                title=target.title or binding.title,
            )
        if targets:
            raise BrowserTargetError(
                f"the bound browser page (tab {binding.tab_index}) is no longer open."
            )
        raise BrowserTargetError(
            f"the bound browser page (tab {binding.tab_index}) is no longer open."
        )

    # ── element targeting ──

    def resolve_element(
        self,
        page: BrowserPage,
        *,
        ref: str = "",
        role: str = "",
        name: str = "",
        text: str = "",
        description: str = "",
        prefer_roles: tuple[str, ...] = (),
    ) -> BrowserElement:
        """Identify the element an action is meant to target.

        Precedence: an explicit snapshot reference, then a semantic match on
        role / accessible name / label text. A match must be unambiguous — if
        several elements match, or none do, the action is refused rather than
        applied to a guess. ``prefer_roles`` narrows an ambiguous match to the
        roles an action can legitimately use (a text field for typing, a
        clickable control for clicking). Coordinates are never used.
        """
        if ref:
            for element in page.elements:
                if element.ref == ref:
                    return element
            raise BrowserElementError(
                f"the element reference '{ref}' is no longer on the page; re-inspect "
                "the page before acting."
            )

        if not (role or name or text):
            raise BrowserElementError(
                "the intended element was not identified; name it by role, label, or "
                "visible text (refusing to act blind)."
            )

        candidates = [
            element
            for element in page.elements
            if element.matches(role=role, name=name, text=text)
        ]
        usable = [element for element in candidates if not element.disabled]
        candidates = usable or candidates

        if not candidates:
            wanted = description or name or role or text
            raise BrowserElementError(
                f"no element on the page matches '{wanted}'; refusing to guess."
            )
        if len(candidates) > 1 and prefer_roles:
            preferred = [e for e in candidates if e.role in prefer_roles]
            if preferred:
                candidates = preferred
        if len(candidates) > 1:
            # Prefer an exact accessible-name match when several look similar.
            if name:
                exact = [
                    element
                    for element in candidates
                    if element.name.strip().casefold() == name.strip().casefold()
                ]
                if len(exact) == 1:
                    return exact[0]
            options = "; ".join(element.describe() for element in candidates[:4])
            raise BrowserElementError(
                f"{len(candidates)} elements match '{description or name or text}' "
                f"({options}); refusing to guess which one."
            )
        return candidates[0]

    # ── actions ──

    def _record(self, outcome: BrowserOutcome) -> BrowserOutcome:
        if outcome.success and (outcome.page.url or outcome.page.title):
            self._page = outcome.page
        return outcome

    def navigate(self, url: str) -> BrowserOutcome:
        outcome = self._record(self.provider.navigate(url))
        if outcome.success:
            self.binding = BrowserBinding(
                session_id=self.session_id,
                tab_index=self.binding.tab_index if self.binding else 0,
                url=outcome.page.url or url,
                title=outcome.page.title,
            )
        return outcome

    def navigate_history(self, direction: str) -> BrowserOutcome:
        """Move through the page's history (``back`` / ``forward``)."""
        outcome = self._record(self.provider.history(direction))
        if outcome.success and self.binding is not None:
            self.binding = BrowserBinding(
                session_id=self.binding.session_id,
                tab_index=self.binding.tab_index,
                url=outcome.page.url or self.binding.url,
                title=outcome.page.title,
            )
        return outcome

    def reload(self) -> BrowserOutcome:
        return self._record(self.provider.reload())

    def observe(self) -> BrowserOutcome:
        """Take a fresh structured observation of the current page."""
        outcome = self._record(self.provider.snapshot())
        if not outcome.success:
            return outcome
        page = outcome.page
        if not page.text and page.refs:
            page = BrowserPage(
                url=page.url,
                title=page.title,
                text="\n".join(page.refs.values()),
                elements=page.elements,
                refs=page.refs,
            )
            outcome = BrowserOutcome.ok(page, message=outcome.message, tool=outcome.metadata.get("tool"))
        return outcome

    def act(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        """Perform one interaction against the currently bound page."""
        self.verify_still_bound(self.binding)
        page = self._page
        if not page.elements:
            observed = self.observe()
            if observed.success:
                page = observed.page
        return self._record(self.provider.act(action, params))

    def find(self, *, text: str = "", regex: str = "") -> BrowserOutcome:
        return self._record(self.provider.find(text=text, regex=regex))

    def wait_for(self, *, text: str = "", time_seconds: float | None = None) -> BrowserOutcome:
        return self._record(self.provider.wait_for(text=text, time=time_seconds))

    def scroll(self, direction: str) -> BrowserOutcome:
        scroll = getattr(self.provider, "scroll", None)
        if not callable(scroll):
            return BrowserOutcome.failed("the browser provider does not support scrolling")
        self.verify_still_bound(self.binding)
        return self._record(scroll(direction))


def create_default_provider(**kwargs: Any) -> BrowserProvider:
    """Create the configured browser provider.

    Selection is by environment so the capability stays provider-independent and
    configurable without touching code:

    ``MAMBA_BROWSER_PROVIDER``
        ``playwright-mcp`` (default) — drive Chrome through the Playwright MCP
        server.
        ``playwright`` — drive Chrome through the Playwright library directly.
    ``MAMBA_BROWSER_HEADLESS``
        ``1``/``true`` for headless (default), ``0``/``false`` to show the window.
    ``MAMBA_BROWSER_CDP_ENDPOINT``
        When set, attach to an already-running Chrome over CDP instead of
        launching a new one.
    ``MAMBA_BROWSER_USER_DATA_DIR``
        Reuse a persistent Chrome profile.
    ``MAMBA_BROWSER_PACKAGE`` / ``MAMBA_BROWSER_ACTION_TIMEOUT``
        MCP server package and per-call timeout.
    """
    import os

    name = (os.environ.get("MAMBA_BROWSER_PROVIDER") or "playwright-mcp").strip().lower()
    headless = (os.environ.get("MAMBA_BROWSER_HEADLESS") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    cdp = (os.environ.get("MAMBA_BROWSER_CDP_ENDPOINT") or "").strip()
    user_data_dir = (os.environ.get("MAMBA_BROWSER_USER_DATA_DIR") or "").strip()
    isolated = (os.environ.get("MAMBA_BROWSER_ISOLATED") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    try:
        timeout = float(os.environ.get("MAMBA_BROWSER_ACTION_TIMEOUT", "180") or 180)
    except ValueError:
        timeout = 180.0

    if name in ("playwright", "playwright-direct", "local"):
        from .playwright_provider import PlaywrightProvider

        return PlaywrightProvider(
            headless=headless,
            cdp_endpoint=cdp,
            user_data_dir=user_data_dir,
            **kwargs,
        )

    from .mcp import PlaywrightMcpProvider

    package = (os.environ.get("MAMBA_BROWSER_PACKAGE") or "").strip()
    options: dict[str, Any] = {
        "browser": "chrome",
        "headless": headless,
        "cdp_endpoint": cdp,
        "user_data_dir": user_data_dir,
        "isolated": isolated,
        "timeout": timeout,
    }
    if package:
        options["package"] = package
    options.update(kwargs)
    return PlaywrightMcpProvider(**options)


__all__ = [
    "BrowserBinding",
    "BrowserSession",
    "create_default_provider",
    "normalize_url",
    "same_page",
]
