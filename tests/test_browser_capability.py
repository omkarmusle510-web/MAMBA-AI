"""Focused tests for Mamba's browser-interaction capability.

The provider is scripted (a fake page model), so these tests exercise Mamba's own
logic: target binding, element identification, risk classification, observation,
and verification through the existing framework.

Real-browser integration tests are opt-in via ``MAMBA_REAL_BROWSER_TESTS=1``.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus
from permissions.policy import DefaultPermissionPolicy
from permissions.types import PermissionDecision, PermissionRequest, PermissionResult, RiskLevel
from skills.browser import BrowserSkill, BrowserTaskHandler
from skills.mixed import create_mixed_task_executor
from tasks.executor import TaskExecutor
from tasks.types import TaskInput
from tools.browser.session import BrowserBinding, BrowserSession, normalize_url, same_page
from tools.browser.snapshot import extract_visible_text, parse_snapshot
from tools.browser.tool import (
    BROWSER_OPERATIONS,
    BrowserTool,
    BrowserToolHandler,
    browser_operation_metadata,
    is_consequential,
)
from tools.browser.types import (
    BrowserAction,
    BrowserElement,
    BrowserElementError,
    BrowserOutcome,
    BrowserPage,
    BrowserTarget,
    BrowserTargetError,
)

_REAL_BROWSER_TESTS = os.environ.get("MAMBA_REAL_BROWSER_TESTS", "").strip() not in (
    "",
    "0",
    "false",
)


# ── a scripted browser provider ─────────────────────────────────────────────


class FakeBrowserProvider:
    """Deterministic provider: pages, elements, tabs, and history."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.pages: dict[str, BrowserPage] = {}
        self.tabs: list[dict[str, Any]] = []
        self.active = 0
        self.url_history: list[str] = []
        self.forward_stack: list[str] = []
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.clicks: list[str] = []
        self.typed: list[tuple[str, str]] = []
        self.available = True
        self.fail_next: str = ""
        self.drift_url: str = ""

    # ── setup helpers ──
    def add_page(
        self,
        url: str,
        title: str,
        *,
        text: str = "",
        elements: tuple[BrowserElement, ...] = (),
        open_tab: bool = True,
    ) -> BrowserPage:
        page = BrowserPage(url=url, title=title, text=text, elements=elements)
        self.pages[normalize_url(url)] = page
        if open_tab:
            self.tabs.append({"url": url, "title": title})
        return page

    def _store(self, page: BrowserPage) -> None:
        """Record a page's current state, keeping elements when none are supplied."""
        existing = self.pages.get(normalize_url(page.url))
        if existing is not None and not page.elements:
            page = BrowserPage(
                url=page.url,
                title=page.title or existing.title,
                text=page.text or existing.text,
                elements=existing.elements,
                refs=existing.refs,
            )
        self.pages[normalize_url(page.url)] = page
        if self.tabs:
            self.tabs[self.active] = {"url": page.url, "title": page.title}
        else:
            self.tabs.append({"url": page.url, "title": page.title})

    def _current_page(self) -> BrowserPage:
        if not self.tabs:
            return BrowserPage()
        url = self.tabs[self.active]["url"]
        if self.drift_url:
            url = self.drift_url
        page = self.pages.get(normalize_url(url))
        if page is None:
            return BrowserPage(url=url, title="", text="")
        return page

    # ── provider protocol ──
    def is_available(self) -> tuple[bool, str]:
        return (True, "fake browser available") if self.available else (
            False,
            "no browser provider is configured",
        )

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def current_page(self) -> tuple[str, str]:
        page = self._current_page()
        return page.url, page.title

    def list_targets(self) -> list[BrowserTarget]:
        return [
            BrowserTarget(
                session_id="fake",
                tab_index=index,
                url=tab["url"],
                title=tab["title"],
                current=index == self.active,
            )
            for index, tab in enumerate(self.tabs)
        ]

    def select_tab(self, tab_index: int) -> None:
        if 0 <= tab_index < len(self.tabs):
            self.active = tab_index

    def navigate(self, url: str) -> BrowserOutcome:
        if self.fail_next:
            message, self.fail_next = self.fail_next, ""
            return BrowserOutcome.failed(message)
        page = self.pages.get(normalize_url(url))
        if page is None:
            return BrowserOutcome.failed(f"could not reach '{url}'")
        self.url_history.append(url)
        self.forward_stack.clear()
        self._store(page)
        return BrowserOutcome.ok(page, message=f"Navigated to {page.url}")

    def history(self, direction: str) -> BrowserOutcome:
        if direction == "back":
            if len(self.url_history) < 2:
                return BrowserOutcome.failed("no previous page in history")
            self.forward_stack.append(self.url_history.pop())
            url = self.url_history[-1]
        else:
            if not self.forward_stack:
                return BrowserOutcome.failed("no next page in history")
            url = self.forward_stack.pop()
            self.url_history.append(url)
        page = self.pages.get(normalize_url(url), BrowserPage(url=url))
        self._store(page)
        return BrowserOutcome.ok(page, message=f"Moved {direction}")

    def reload(self) -> BrowserOutcome:
        page = self._current_page()
        return BrowserOutcome.ok(page, message="Reloaded the page")

    def snapshot(self) -> BrowserOutcome:
        return BrowserOutcome.ok(self._current_page(), tool="snapshot")

    def page_text(self) -> BrowserOutcome:
        page = self._current_page()
        return BrowserOutcome.ok(BrowserPage(url=page.url, title=page.title, text=page.text))

    def find(self, *, text: str = "", regex: str = "") -> BrowserOutcome:
        page = self._current_page()
        needle = text.casefold()
        matches = [line for line in page.text.splitlines() if needle in line.casefold()]
        if not matches:
            return BrowserOutcome.failed(f"'{text}' was not found on the page")
        return BrowserOutcome.ok(
            BrowserPage(url=page.url, title=page.title, text="\n".join(matches))
        )

    def wait_for(self, *, text: str = "", time: float | None = None) -> BrowserOutcome:
        page = self._current_page()
        if text and text.casefold() not in page.text.casefold():
            return BrowserOutcome.failed(f"timed out waiting for '{text}'")
        return BrowserOutcome.ok(page, message="Wait completed")

    def scroll(self, direction: str) -> BrowserOutcome:
        page = self._current_page()
        return BrowserOutcome.ok(page, message=f"Scrolled {direction}")

    def act(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        self.actions.append((action, dict(params)))
        page = self._current_page()
        ref = str(params.get("ref") or "")
        element = next((e for e in page.elements if e.ref == ref), None)

        if action == "click":
            if element is None:
                return BrowserOutcome.failed("target element was not found")
            self.clicks.append(element.name or element.ref)
            target_url = element.url
            if target_url:
                return self.navigate(target_url)
            return BrowserOutcome.ok(page, message=f"Clicked {element.describe()}")

        if action == "type":
            if element is None:
                return BrowserOutcome.failed("target field was not found")
            text = str(params.get("text") or "")
            self.typed.append((element.name or element.ref, text))
            updated = BrowserPage(
                url=page.url,
                title=page.title,
                text=page.text,
                elements=tuple(
                    BrowserElement(
                        ref=e.ref,
                        role=e.role,
                        name=e.name,
                        url=e.url,
                        element_type=e.element_type,
                        value=text if e.ref == element.ref else e.value,
                        disabled=e.disabled,
                    )
                    for e in page.elements
                ),
            )
            self.pages[normalize_url(page.url)] = updated
            return BrowserOutcome.ok(updated, message=f"Typed into {element.describe()}")

        if action == "clear":
            return BrowserOutcome.ok(page, message="Cleared the field")
        if action == "press_key":
            return BrowserOutcome.ok(page, message=f"Pressed {params.get('key')}")
        if action == "select_option":
            return BrowserOutcome.ok(page, message="Selected the option")
        if action == "hover":
            return BrowserOutcome.ok(page, message="Hovered")
        return BrowserOutcome.failed(f"unsupported action '{action}'")


def _link(ref: str, name: str, url: str) -> BrowserElement:
    return BrowserElement(ref=ref, role="link", name=name, url=url)


def _button(ref: str, name: str) -> BrowserElement:
    return BrowserElement(ref=ref, role="button", name=name)


def _textbox(ref: str, name: str) -> BrowserElement:
    return BrowserElement(ref=ref, role="textbox", name=name, element_type="text")


def _home_provider() -> FakeBrowserProvider:
    provider = FakeBrowserProvider()
    provider.add_page(
        "https://github.com/",
        "GitHub: Let's build from here",
        text="GitHub Where the world builds software",
        elements=(
            _link("e1", "Sign in", "https://github.com/login"),
            _link("e2", "Sign up", "https://github.com/signup"),
            _textbox("e3", "Search or jump to"),
        ),
        open_tab=True,
    )
    provider.add_page(
        "https://github.com/signup",
        "Join GitHub",
        text="Join GitHub Create your account",
        elements=(_textbox("g1", "Email"),),
        open_tab=False,  # reachable by clicking "Sign up", not open by default
    )
    provider.add_page(
        "https://github.com/login",
        "Sign in to GitHub",
        text="Sign in to GitHub Username or email address Password",
        elements=(
            _textbox("f1", "Username or email address"),
            _textbox("f2", "Password"),
            _button("f3", "Sign in"),
        ),
        open_tab=False,
    )
    return provider


def _search_provider() -> FakeBrowserProvider:
    provider = FakeBrowserProvider()
    provider.add_page(
        "https://www.youtube.com/",
        "YouTube",
        text="YouTube Search",
        elements=(
            _textbox("s1", "Search"),
            _button("s2", "Search"),
        ),
        open_tab=True,
    )
    provider.add_page(
        "https://www.youtube.com/results?search_query=mamba+ai",
        "mamba ai - YouTube",
        text="mamba ai results 1 Mamba AI explained 2 Mamba architecture",
        elements=(_link("r1", "Mamba AI explained", "https://youtube.com/watch?v=1"),),
    )
    return provider


def _session(provider: FakeBrowserProvider) -> BrowserSession:
    return BrowserSession(provider=provider)


def _handler(provider: FakeBrowserProvider) -> BrowserToolHandler:
    return BrowserToolHandler(session=_session(provider))


class StaticPlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan
        self.calls = 0

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        self.calls += 1
        return self._plan


def _brain(provider: FakeBrowserProvider, *steps: PlanStep, permissions: Any = None) -> Brain:
    handler = BrowserTaskHandler(provider=provider)
    intents = {
        intent: handler
        for intent in (
            "open_url_in_browser",
            "navigate_browser",
            "browser_back",
            "browser_forward",
            "browser_reload",
            "inspect_page",
            "read_page",
            "click_element",
            "type_text_in_page",
            "browser_scroll",
            "list_browser_targets",
            "attach_browser",
        )
    }
    return Brain(
        planner=StaticPlanner(ExecutionPlan(steps=tuple(steps))),
        executor=TaskExecutor(handlers=intents),
        permissions=permissions or DefaultPermissionPolicy(),
    )


# ── 1. connection / provider selection ──────────────────────────────────────


def test_provider_selection_is_configurable(monkeypatch):
    """The provider is chosen by configuration, so another backend can be added."""
    from tools.browser.session import create_default_provider

    monkeypatch.delenv("MAMBA_BROWSER_PROVIDER", raising=False)
    assert type(create_default_provider()).__name__ == "PlaywrightMcpProvider"

    monkeypatch.setenv("MAMBA_BROWSER_PROVIDER", "playwright")
    assert type(create_default_provider()).__name__ == "PlaywrightProvider"


def test_mcp_provider_defaults_to_chrome_and_launch_mode():
    from tools.browser.mcp import PlaywrightMcpProvider

    provider = PlaywrightMcpProvider(headless=True)
    assert provider.mode == "launch"
    command = provider._resolved_command()
    assert "--browser" in command and "chrome" in command
    assert "--headless" in command

    attached = PlaywrightMcpProvider(cdp_endpoint="http://127.0.0.1:9222")
    assert attached.mode == "connect"
    assert "--cdp-endpoint" in attached._resolved_command()


def test_browser_unavailable_is_reported_honestly():
    provider = FakeBrowserProvider()
    provider.available = False
    handler = _handler(provider)

    output = handler.run(_tool_input("inspect_page", {}))

    assert output.success is False
    assert "no browser provider is configured" in _message(output)


# ── 2. navigation ───────────────────────────────────────────────────────────


def test_open_url_binds_the_page():
    provider = _home_provider()
    handler = _handler(provider)

    output = handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    assert output.success is True
    assert output.metadata["page_url"] == "https://github.com/"
    assert output.metadata["page_title"].startswith("GitHub")
    assert output.metadata["session_id"] == "default"
    assert output.metadata["bound_url"] == "https://github.com/"


def test_navigate_and_history():
    provider = _home_provider()
    handler = _handler(provider)

    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))
    forward = handler.run(_tool_input("navigate_browser", {"url": "https://github.com/login"}))
    assert forward.metadata["page_url"] == "https://github.com/login"

    back = handler.run(_tool_input("browser_back", {}))
    assert back.success is True
    assert back.metadata["page_url"] == "https://github.com/"

    again = handler.run(_tool_input("browser_forward", {}))
    assert again.success is True
    assert again.metadata["page_url"] == "https://github.com/login"


def test_current_page_and_reload():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    current = handler.run(_tool_input("inspect_page", {}))
    assert current.metadata["page_url"] == "https://github.com/"

    reloaded = handler.run(_tool_input("browser_reload", {}))
    assert reloaded.success is True
    assert reloaded.metadata["page_url"] == "https://github.com/"


def test_navigation_without_url_is_refused_before_executing():
    provider = _home_provider()
    brain = _brain(
        provider,
        PlanStep(description="open a page", intent="open_url_in_browser", metadata={}),
    )

    result = brain.run("open a page")

    assert result.status == ResultStatus.FAILED
    assert any("no URL was provided" in o.content for o in result.observations)


# ── 3. page understanding ───────────────────────────────────────────────────


def test_inspect_page_returns_structured_content():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("inspect_page", {}))

    assert output.success is True
    assert "URL: https://github.com/" in output.result
    assert "GitHub" in output.result
    assert "Interactive elements:" in output.result
    assert output.metadata["element_count"] == 3
    assert "e1" in output.metadata["element_refs"]
    assert output.metadata["page_text"]


def test_links_buttons_and_fields_are_listed_separately():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    links = handler.run(_tool_input("browser_links", {}))
    assert "Sign in" in links.result
    assert "Search or jump to" not in links.result

    fields = handler.run(_tool_input("browser_fields", {}))
    assert "Search or jump to" in fields.result

    buttons = handler.run(_tool_input("browser_buttons", {}))
    assert buttons.success is True


def test_read_page_returns_visible_text():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("read_page", {}))
    assert "Where the world builds software" in output.result


def test_find_on_page_locates_text():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    found = handler.run(_tool_input("find_on_page", {"query": "world builds"}))
    assert found.success is True
    assert "world builds" in found.result

    missing = handler.run(_tool_input("find_on_page", {"query": "nonexistent-term"}))
    assert missing.success is False


# ── 4. element targeting ────────────────────────────────────────────────────


def test_click_targets_by_accessible_name_not_coordinates():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(
        _tool_input("click_element", {"role": "link", "name": "Sign in"})
    )

    assert output.success is True
    assert provider.clicks == ["Sign in"]
    assert output.metadata["target_element"] == 'link "Sign in" -> https://github.com/login'


def test_click_by_visible_text_when_role_is_unknown():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("click_element", {"text": "Sign up"}))

    assert output.success is True
    assert provider.clicks == ["Sign up"]


def test_ambiguous_target_is_refused_not_guessed():
    provider = FakeBrowserProvider()
    provider.add_page(
        "https://example.com/form",
        "Form",
        elements=(_button("b1", "Submit"), _button("b2", "Submit")),
        open_tab=True,
    )
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://example.com/form"}))

    output = handler.run(_tool_input("click_element", {"role": "button", "name": "Submit"}))

    assert output.success is False
    assert provider.clicks == []
    assert "refusing to guess" in _message(output)


def test_missing_element_is_refused_not_guessed():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("click_element", {"name": "Buy now"}))

    assert output.success is False
    assert provider.clicks == []
    assert "refusing to guess" in _message(output)


def test_click_without_any_identifier_is_refused():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("click_element", {}))

    assert output.success is False
    assert provider.clicks == []
    assert "refusing to act blind" in _message(output)


def test_stale_element_reference_is_refused():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("click_element", {"ref": "e999"}))

    assert output.success is False
    assert provider.clicks == []
    assert "no longer on the page" in _message(output)


# ── 5. interaction ──────────────────────────────────────────────────────────


def test_type_into_identified_field():
    provider = _search_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://www.youtube.com/"}))

    output = handler.run(
        _tool_input("type_text_in_page", {"name": "Search", "text": "Mamba AI"})
    )

    assert output.success is True
    assert provider.typed == [("Search", "Mamba AI")]


def test_type_without_text_is_refused():
    provider = _search_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://www.youtube.com/"}))

    output = handler.run(_tool_input("type_text_in_page", {"name": "Search"}))

    assert output.success is False
    assert provider.typed == []


def test_scroll_reports_the_page_after_scrolling():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    output = handler.run(_tool_input("browser_scroll", {"direction": "down"}))
    assert output.success is True
    assert "Scrolled down" in _message(output)

    bad = handler.run(_tool_input("browser_scroll", {"direction": "sideways"}))
    assert bad.success is False


def test_search_flow_types_and_clicks():
    """'Search YouTube for Mamba AI' = type into the box, click search, observe results."""
    provider = _search_provider()
    brain = _brain(
        provider,
        PlanStep(description="open YouTube", intent="open_url_in_browser", metadata={"url": "https://www.youtube.com/"}),
        PlanStep(
            description="type the query",
            intent="type_text_in_page",
            metadata={"name": "Search", "text": "Mamba AI", "expected": {"contains": "Search"}},
        ),
        PlanStep(
            description="click the search button",
            intent="click_element",
            metadata={
                "role": "button",
                "name": "Search",
                "consequential": False,
                "expected": {"contains": "mamba ai"},
            },
        ),
    )

    result = brain.run("Search YouTube for Mamba AI")

    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    assert provider.typed == [("Search", "Mamba AI")]
    assert provider.clicks == ["Search"]


# ── 6/7. observation and verification ───────────────────────────────────────


def test_verification_confirms_expected_text_after_navigation():
    provider = _search_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="open the results page",
            intent="open_url_in_browser",
            metadata={
                "url": "https://www.youtube.com/results?search_query=mamba+ai",
                "expected": {"contains": "Mamba AI explained"},
            },
        ),
    )

    result = brain.run("open the search results")

    assert result.status == ResultStatus.COMPLETED
    verified = [o for o in result.observations if o.metadata.get("verified")]
    assert verified, "the outcome was not verified"
    assert "Mamba AI explained" in verified[-1].content


def test_verification_fails_when_expected_text_is_absent():
    """Clicking is not success: the expected result must actually appear."""
    provider = _search_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="open the results page",
            intent="open_url_in_browser",
            metadata={
                "url": "https://www.youtube.com/results?search_query=mamba+ai",
                "expected": {"contains": "a video that does not exist"},
            },
        ),
    )

    result = brain.run("open the search results")

    assert result.status == ResultStatus.FAILED
    assert any(
        "verification failed" in o.content.lower() for o in result.observations
    ), [o.content for o in result.observations]


def test_verification_checks_the_url():
    provider = _search_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="open YouTube",
            intent="open_url_in_browser",
            metadata={"url": "https://www.youtube.com/", "expect_url": "youtube.com"},
        ),
    )
    assert brain.run("open YouTube").status == ResultStatus.COMPLETED

    provider2 = _search_provider()
    brain2 = _brain(
        provider2,
        PlanStep(
            description="open YouTube",
            intent="open_url_in_browser",
            metadata={"url": "https://www.youtube.com/", "expect_url": "github.com"},
        ),
    )
    failed = brain2.run("open YouTube")
    assert failed.status == ResultStatus.FAILED
    assert any("page URL" in o.content for o in failed.observations)


def test_interaction_without_an_expected_outcome_is_reported_unverified():
    """A click that states no outcome is reported unverified, never assumed a success."""
    provider = _home_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="open GitHub",
            intent="open_url_in_browser",
            metadata={"url": "https://github.com/"},
        ),
        PlanStep(
            description="click the sign in link",
            intent="click_element",
            metadata={"role": "link", "name": "Sign in"},
        ),
    )

    result = brain.run("open GitHub and click sign in")

    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    assert provider.clicks == ["Sign in"]
    unverified = [o for o in result.observations if o.metadata.get("action") == "verification_unverified"]
    assert unverified, "the unverifiable interaction was not reported honestly"
    assert "not independently verified" in unverified[-1].content
    assert not any(o.metadata.get("verified") for o in result.observations)


# ── 9. target safety ────────────────────────────────────────────────────────


def test_stale_page_binding_is_refused():
    """If the page drifts away from the bound URL, the action is refused."""
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    # The page navigates elsewhere behind Mamba's back.
    provider.drift_url = "https://github.com/signup"

    output = handler.run(_tool_input("click_element", {"role": "link", "name": "Sign in"}))

    assert output.success is False
    assert provider.clicks == []
    assert "refusing to act on a different page" in _message(output)


def test_closed_page_is_refused():
    provider = _home_provider()
    handler = _handler(provider)
    handler.run(_tool_input("open_url_in_browser", {"url": "https://github.com"}))

    provider.tabs.clear()

    output = handler.run(_tool_input("click_element", {"role": "link", "name": "Sign in"}))
    assert output.success is False
    assert provider.clicks == []


def test_ambiguous_tab_selection_is_refused():
    provider = _home_provider()
    provider.add_page("https://github.com/issues", "Issues", open_tab=True)
    handler = _handler(provider)

    output = handler.run(_tool_input("attach_browser", {"title": "GitHub"}))

    assert output.success is False
    assert "multiple browser pages match" in _message(output)


def test_attach_binds_a_specific_tab():
    provider = FakeBrowserProvider()
    provider.add_page("https://github.com/", "GitHub", open_tab=True)
    provider.add_page("https://github.com/issues", "Issues", open_tab=True)
    handler = _handler(provider)

    output = handler.run(_tool_input("attach_browser", {"tab_index": 1}))

    assert output.success is True
    assert output.metadata["tab_index"] == 1
    assert output.metadata["page_url"] == "https://github.com/issues"


def test_list_targets_reports_open_pages():
    provider = _home_provider()
    provider.add_page("https://github.com/issues", "Issues", open_tab=True)
    handler = _handler(provider)

    output = handler.run(_tool_input("list_browser_targets", {}))

    assert output.success is True
    assert "tab 0" in output.result
    assert "tab 1" in output.result
    assert len(output.metadata["targets"]) == 3  # github.com, login (from the click), Issues


def test_bind_rejects_a_page_that_is_not_open():
    provider = _home_provider()
    session = _session(provider)

    with pytest.raises(BrowserTargetError):
        session.bind(url="https://not-open.example.com/")


# ── 10. permission boundary ─────────────────────────────────────────────────


def test_read_and_navigation_are_low_risk():
    for action in ("open", "navigate", "back", "forward", "reload", "inspect_page",
                   "read_page", "browser_links", "browser_fields", "find_text", "list_targets"):
        metadata = browser_operation_metadata({"action": action})
        assert metadata["risk_level"] == RiskLevel.LOW, action
        assert metadata["read_only"] is True or action in (
            "open", "navigate", "back", "forward", "reload"
        )


def test_ordinary_interaction_is_medium_risk():
    for action in ("click", "type", "clear", "press_key", "scroll", "select_option"):
        metadata = browser_operation_metadata({"action": action, "name": "Next page"})
        assert metadata["risk_level"] == RiskLevel.MEDIUM, action
        assert metadata["externally_visible"] is True


def test_consequential_actions_escalate_and_require_approval():
    metadata = browser_operation_metadata(
        {"action": "click", "name": "Send", "description": 'Click the "Send" button'}
    )
    assert metadata["risk_level"] == RiskLevel.HIGH
    assert metadata["irreversible"] is True
    assert metadata["externally_visible"] is True

    request = PermissionRequest(
        action=metadata["action"],
        tool_name=metadata.get("tool_name", "browser_action"),
        risk_level=metadata["risk_level"],
        metadata=metadata,
    )
    assert DefaultPermissionPolicy().evaluate(request).decision == PermissionDecision.ASK


def test_explicit_consequential_flag_escalates():
    assert is_consequential({"action": "type", "consequential": True})
    assert is_consequential({"action": "click", "safety": "consequential"})
    assert is_consequential({"action": "click", "description": "submit the form"})
    assert not is_consequential({"action": "click", "description": "open the docs link"})


def test_high_risk_browser_action_asks_then_resumes_on_approval():
    """Consequential browser actions use Mamba's existing approval flow."""
    provider = _home_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="click the Send button",
            intent="click_element",
            metadata={"role": "button", "name": "Sign in", "consequential": True},
        ),
    )

    first = brain.run("click Send")
    assert first.status == ResultStatus.FAILED
    assert any(
        o.metadata.get("awaiting_approval") for o in first.observations
    ), [o.content for o in first.observations]
    assert provider.clicks == []

    second = brain.run("yes")
    assert provider.clicks == ["Sign in"]
    assert second.status == ResultStatus.COMPLETED


def test_voice_cannot_approve_a_consequential_browser_action():
    provider = _home_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="click the Send button",
            intent="click_element",
            metadata={"role": "button", "name": "Sign in", "consequential": True},
        ),
    )
    brain.run("click Send")

    from core.types import UserRequest

    result = brain.run(UserRequest(goal="yes", metadata={"input_modality": "voice"}))

    assert provider.clicks == []
    assert any(
        "confirm on screen" in o.content.lower() for o in result.observations
    ), [o.content for o in result.observations]


# ── plumbing / regression guards ────────────────────────────────────────────


def test_browser_intents_are_wired_into_the_mixed_executor():
    executor = create_mixed_task_executor()
    handlers = executor.handlers or {}
    for intent in (
        "open_url_in_browser",
        "navigate_browser",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "inspect_page",
        "read_page",
        "browser_links",
        "browser_fields",
        "find_on_page",
        "click_element",
        "type_text_in_page",
        "browser_scroll",
        "list_browser_targets",
        "attach_browser",
    ):
        assert intent in handlers, f"{intent} is not wired"


def test_tavily_search_and_browser_are_separate_capabilities():
    executor = create_mixed_task_executor()
    handlers = executor.handlers or {}
    assert type(handlers["web_search"]).__name__ == "WebTaskHandler"
    assert type(handlers["open_url_in_browser"]).__name__ == "BrowserTaskHandler"

    from core.capabilities import default_capability_registry

    registry = default_capability_registry()
    assert registry.find_capability_for_action("web_search").capability_id == "web"
    assert registry.find_capability_for_action("open_url_in_browser").capability_id == "browser"


def test_task_handler_maps_intents_to_actions():
    handler = BrowserTaskHandler(provider=FakeBrowserProvider())
    mapping = {
        "open_url_in_browser": "open",
        "navigate_browser": "navigate",
        "browser_back": "back",
        "browser_forward": "forward",
        "browser_reload": "reload",
        "inspect_page": "inspect_page",
        "click_element": "click",
        "type_text_in_page": "type",
        "browser_scroll": "scroll",
    }
    for intent, expected in mapping.items():
        params = handler._params(
            TaskInput(
                step_id="s",
                description="d",
                intent=intent,
                execution_id="e",
                goal="g",
                step_metadata={},
            )
        )
        assert params["action"] == expected, intent


def test_metadata_for_a_step_comes_from_the_tool_layer():
    handler = BrowserTaskHandler(provider=FakeBrowserProvider())
    metadata = handler.get_metadata(
        TaskInput(
            step_id="s",
            description="click Send",
            intent="click_element",
            execution_id="e",
            goal="g",
            step_metadata={"action": "click", "name": "Send", "consequential": True},
        )
    )
    assert metadata["risk_level"] == RiskLevel.HIGH
    assert metadata["tool_name"] == "browser_action"


# ── snapshot parsing (page understanding without a live browser) ────────────


SAMPLE_SNAPSHOT = """\
- generic [active] [ref=e1]:
  - heading "Example Domain" [level=1] [ref=e2]
  - paragraph [ref=e3]: This domain is for use in documentation examples.
  - link "Learn more" [ref=e4] [cursor=pointer]:
    - /url: https://iana.org/help/example-domains
  - search [ref=e5]:
    - searchbox "Search the docs" [ref=e6]
    - button "Search" [ref=e7]
"""


def test_snapshot_parses_roles_names_refs_and_urls():
    elements, refs = parse_snapshot(SAMPLE_SNAPSHOT)

    by_ref = {element.ref: element for element in elements}
    assert by_ref["e2"].role == "heading"
    assert by_ref["e2"].name == "Example Domain"
    assert by_ref["e4"].role == "link"
    assert by_ref["e4"].url == "https://iana.org/help/example-domains"
    assert by_ref["e6"].role == "searchbox"
    assert by_ref["e6"].name == "Search the docs"
    assert by_ref["e6"].context == ("Search the docs",) or "e6" in refs


def test_snapshot_visible_text_extraction():
    text = extract_visible_text(SAMPLE_SNAPSHOT)
    assert "Example Domain" in text
    assert "documentation examples" in text
    assert "Learn more" in text


def test_inline_snapshot_in_an_mcp_response_is_parsed():
    from tools.browser.mcp import extract_page

    response = (
        "### Page\n"
        "- Page URL: https://www.iana.org/help/example-domains\n"
        "- Page Title: Example Domains\n"
        "### Snapshot\n"
        "```yaml\n"
        "- generic [ref=f1e1]:\n"
        '  - link "Instructions" [ref=f1e2] [cursor=pointer]:\n'
        "    - /url: /help\n"
        "```\n"
    )
    url, title, snapshot = extract_page(response)
    assert url == "https://www.iana.org/help/example-domains"
    assert title == "Example Domains"
    assert "ref=f1e2" in snapshot

    elements, refs = parse_snapshot(snapshot)
    by_ref = {element.ref: element for element in elements}
    assert by_ref["f1e2"].url == "/help"
    assert by_ref["f1e2"].name == "Instructions"


def test_url_comparison_treats_www_and_scheme_as_same_page():
    assert same_page("https://www.github.com/", "github.com")
    assert same_page("http://example.com", "https://example.com/")
    assert not same_page("https://github.com/", "https://github.com/issues")
    assert normalize_url("https://www.Example.com/") == "example.com"


# ── opt-in real browser integration ─────────────────────────────────────────


@pytest.mark.skipif(
    not _REAL_BROWSER_TESTS,
    reason="real browser interaction; set MAMBA_REAL_BROWSER_TESTS=1 to run",
)
def test_real_browser_navigate_inspect_and_verify():
    """End-to-end against a real Chrome via Playwright MCP."""
    provider = _real_provider()
    session = BrowserSession(provider=provider)
    handler = BrowserToolHandler(session=session)
    try:
        opened = handler.run(
            _tool_input("open_url_in_browser", {"url": "https://example.com"})
        )
        assert opened.success is True, opened.result
        assert "example.com" in opened.metadata["page_url"]
        assert "Example" in opened.metadata["page_title"]

        inspected = handler.run(_tool_input("inspect_page", {}))
        assert inspected.success is True
        assert inspected.metadata["page_text"]
    finally:
        provider.stop()


@pytest.mark.skipif(
    not _REAL_BROWSER_TESTS,
    reason="real browser interaction; set MAMBA_REAL_BROWSER_TESTS=1 to run",
)
def test_real_browser_click_and_verify_outcome():
    """Real click, real observation, real verification through Mamba's Brain."""
    provider = _real_provider()
    brain = _brain(
        provider,
        PlanStep(
            description="open example.com",
            intent="open_url_in_browser",
            metadata={"url": "https://example.com", "expect_title": "Example"},
        ),
        PlanStep(
            description="click the more information link",
            intent="click_element",
            metadata={
                "role": "link",
                "name": "More information",
                "expected": {"contains": "iana"},
            },
        ),
    )
    try:
        result = brain.run("open example.com and click the more information link")
        assert result.status == ResultStatus.COMPLETED, result.error or result.output
        assert any(o.metadata.get("verified") for o in result.observations)
    finally:
        provider.stop()


def _real_provider():
    from tools.browser.mcp import PlaywrightMcpProvider

    return PlaywrightMcpProvider(
        browser="chrome",
        headless=os.environ.get("MAMBA_REAL_BROWSER_HEADLESS", "1") not in ("0", "false"),
        isolated=True,
    )


def _tool_input(action: str, extra: dict[str, Any]):
    from tools.types import ToolInput

    params = {"action": action, **extra}
    return ToolInput(arguments=dict(params), metadata=dict(params))


def _message(output: Any) -> str:
    """The text a tool output carries, whether it succeeded or failed."""
    return str(output.result if output.result is not None else (output.error or ""))
