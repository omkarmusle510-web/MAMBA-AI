"""Direct Playwright provider: a browser backend that needs no Node.js.

This is the same Playwright engine the MCP server drives, used through the
Python library when the MCP server is unavailable (or when
``MAMBA_BROWSER_PROVIDER=playwright`` is selected). It exists so the browser
capability degrades gracefully instead of disappearing, and so the layer above
stays provider-independent.

The provider runs on a dedicated worker thread. Keeping Playwright there means
Mamba's transport (an async server) never has the browser's sync API on its
event loop, and one long browser call cannot stall unrelated Mamba work.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from .snapshot import INTERACTIVE_ROLES
from .types import (
    BrowserElement,
    BrowserOutcome,
    BrowserPage,
    BrowserTarget,
    BrowserTargetError,
)


class _Worker:
    """Serializes Playwright work onto one dedicated thread."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Callable[[], Any], dict[str, Any]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def _run(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                return
            func, box = item
            try:
                box["result"] = func()
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
                box["error"] = exc

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name="mamba-playwright")
            self._thread.start()

    def submit(self, func: Callable[[], Any], timeout: float = 180.0) -> Any:
        self.ensure_started()
        box: dict[str, Any] = {}
        self._jobs.put((func, box))
        deadline = timeout
        step = 0.05
        waited = 0.0
        while waited < deadline:
            if "result" in box:
                return box["result"]
            if "error" in box:
                raise box["error"]
            threading.Event().wait(step)
            waited += step
        raise BrowserTargetError("the browser provider did not respond in time")

    def shutdown(self) -> None:
        self._jobs.put(None)


def _clean_name(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in ("None", "null") else text


def _role_of(handle: Any) -> str:
    try:
        return _clean_name(handle.get_attribute("role")) or _clean_name(handle.evaluate("e => e.tagName")).lower()
    except Exception:
        return ""


class PlaywrightProvider:
    """Browser provider implemented directly on the Playwright Python library."""

    provider_name = "playwright"

    def __init__(
        self,
        *,
        headless: bool = True,
        cdp_endpoint: str = "",
        user_data_dir: str = "",
        browser: str = "chrome",
        timeout_ms: int = 30000,
        navigation_timeout_ms: int = 60000,
        viewport: tuple[int, int] = (1280, 800),
        **_ignored: Any,
    ) -> None:
        self._headless = headless
        self._cdp_endpoint = cdp_endpoint
        self._user_data_dir = user_data_dir
        self._browser_channel = browser
        self._timeout_ms = timeout_ms
        self._navigation_timeout_ms = navigation_timeout_ms
        self._viewport = viewport
        self._worker = _Worker()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    # ── availability ──

    def is_available(self) -> tuple[bool, str]:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError as exc:
            return False, f"the Playwright Python package is not installed: {exc}"
        return True, f"Playwright is available ({self._browser_channel} channel)."

    # ── lifecycle (must run on the worker thread) ──

    def start(self) -> None:
        self._worker.submit(self._start_sync, timeout=120.0)

    def _start_sync(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        from playwright.sync_api import sync_playwright

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        if self._cdp_endpoint:
            if self._browser is None or not self._browser.is_connected():
                self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_endpoint)
            contexts = self._browser.contexts
            if not contexts:
                raise BrowserTargetError("the attached Chrome session has no browser context")
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[-1] if pages else self._context.new_page()
        else:
            if self._context is None:
                if self._user_data_dir:
                    self._context = self._playwright.chromium.launch_persistent_context(
                        self._user_data_dir,
                        channel=self._browser_channel,
                        headless=self._headless,
                        viewport={"width": self._viewport[0], "height": self._viewport[1]},
                    )
                    self._browser = self._context.browser
                else:
                    self._browser = self._playwright.chromium.launch(
                        channel=self._browser_channel,
                        headless=self._headless,
                    )
                    self._context = self._browser.new_context(
                        viewport={"width": self._viewport[0], "height": self._viewport[1]}
                    )
            pages = [page for page in self._context.pages if not page.is_closed()]
            self._page = pages[-1] if pages else self._context.new_page()

        self._page.set_default_timeout(self._timeout_ms)
        self._page.set_default_navigation_timeout(self._navigation_timeout_ms)

    def stop(self) -> None:
        def _stop() -> None:
            try:
                if self._cdp_endpoint:
                    # Never close a browser Mamba did not launch.
                    if self._playwright is not None:
                        self._playwright.stop()
                else:
                    if self._context is not None:
                        self._context.close()
                    if self._browser is not None:
                        self._browser.close()
                    if self._playwright is not None:
                        self._playwright.stop()
            except Exception:
                pass
            finally:
                self._playwright = None
                self._browser = None
                self._context = None
                self._page = None

        try:
            self._worker.submit(_stop, timeout=30.0)
        except Exception:
            pass
        self._worker.shutdown()

    # ── page helpers (worker thread) ──

    def _require_page(self) -> Any:
        self._start_sync()
        if self._page is None or self._page.is_closed():
            raise BrowserTargetError("there is no live browser page to act on")
        return self._page

    def _current(self) -> tuple[str, str]:
        def _read() -> tuple[str, str]:
            page = self._require_page()
            return page.url, (page.title() or "")

        return self._worker.submit(_read)

    def current_page(self) -> tuple[str, str]:
        return self._current()

    def list_targets(self) -> list[BrowserTarget]:
        def _list() -> list[BrowserTarget]:
            self._start_sync()
            targets: list[BrowserTarget] = []
            contexts = [self._context] if self._context is not None else []
            if self._browser is not None and not contexts:
                contexts = list(self._browser.contexts)
            for context in contexts:
                if context is None:
                    continue
                for index, page in enumerate(context.pages):
                    if page.is_closed():
                        continue
                    targets.append(
                        BrowserTarget(
                            session_id="playwright",
                            tab_index=index,
                            url=page.url,
                            title=page.title() or "",
                            current=page is self._page,
                        )
                    )
            if not targets and self._page is not None and not self._page.is_closed():
                targets.append(
                    BrowserTarget(
                        session_id="playwright",
                        tab_index=0,
                        url=self._page.url,
                        title=self._page.title() or "",
                        current=True,
                    )
                )
            return targets

        return self._worker.submit(_list)

    def select_tab(self, tab_index: int) -> None:
        def _select() -> None:
            self._start_sync()
            pages = [p for p in (self._context.pages if self._context else []) if not p.is_closed()]
            if tab_index < 0 or tab_index >= len(pages):
                raise BrowserTargetError(f"browser tab {tab_index} is not open")
            page = pages[tab_index]
            page.bring_to_front()
            self._page = page

        self._worker.submit(_select)

    # ── page observation (worker thread) ──

    def _page_model(self, page: Any) -> BrowserPage:
        url = page.url or ""
        title = ""
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        text = ""
        elements: list[BrowserElement] = []
        refs: dict[str, str] = {}
        try:
            text = page.inner_text("body", timeout=5000) or ""
        except Exception:
            text = ""
        try:
            handles = page.query_selector_all(
                "a, button, input, select, textarea, [role], [contenteditable=true]"
            )
            for index, handle in enumerate(handles[:200]):
                try:
                    if not handle.is_visible():
                        continue
                    ref = f"p{index + 1}"
                    role = _role_of(handle)
                    name = ""
                    try:
                        name = _clean_name(handle.inner_text() or "")[:120]
                    except Exception:
                        name = ""
                    if not name:
                        name = _clean_name(handle.get_attribute("aria-label")) or _clean_name(
                            handle.get_attribute("placeholder")
                        )
                    href = ""
                    try:
                        href = _clean_name(handle.get_attribute("href"))
                    except Exception:
                        href = ""
                    value = ""
                    try:
                        value = _clean_name(handle.input_value() or "")
                    except Exception:
                        value = ""
                    disabled = False
                    try:
                        disabled = bool(handle.is_disabled())
                    except Exception:
                        disabled = False
                    element = BrowserElement(
                        ref=ref,
                        role=role,
                        name=name,
                        url=href,
                        element_type=_clean_name(handle.get_attribute("type")),
                        value=value,
                        disabled=disabled,
                    )
                    elements.append(element)
                    refs[ref] = element.describe()
                    try:
                        handle.evaluate("(e, r) => e.setAttribute('data-mamba-ref', r)", ref)
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass
        return BrowserPage(
            url=url, title=title, text=text, elements=tuple(elements), refs=refs
        )

    def _observe(self) -> BrowserOutcome:
        def _read() -> BrowserOutcome:
            page = self._require_page()
            return BrowserOutcome.ok(self._page_model(page), tool="observe")

        return self._worker.submit(_read)

    def snapshot(self) -> BrowserOutcome:
        return self._observe()

    def page_text(self) -> BrowserOutcome:
        return self._observe()

    # ── navigation ──

    def navigate(self, url: str) -> BrowserOutcome:
        def _go() -> BrowserOutcome:
            page = self._require_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return BrowserOutcome.ok(self._page_model(page), message=f"Navigated to {page.url}")

        try:
            return self._worker.submit(_go)
        except Exception as exc:
            return BrowserOutcome.failed(f"could not navigate to '{url}': {exc}")

    def history(self, direction: str) -> BrowserOutcome:
        def _history() -> BrowserOutcome:
            page = self._require_page()
            if direction == "back":
                page.go_back(wait_until="domcontentloaded")
            elif direction == "forward":
                page.go_forward(wait_until="domcontentloaded")
            else:
                return BrowserOutcome.failed(f"unsupported history direction '{direction}'")
            return BrowserOutcome.ok(self._page_model(page), message=f"Moved {direction}")

        try:
            return self._worker.submit(_history)
        except Exception as exc:
            return BrowserOutcome.failed(f"could not navigate {direction}: {exc}")

    def reload(self) -> BrowserOutcome:
        def _reload() -> BrowserOutcome:
            page = self._require_page()
            page.reload(wait_until="domcontentloaded")
            return BrowserOutcome.ok(self._page_model(page), message="Reloaded the page")

        try:
            return self._worker.submit(_reload)
        except Exception as exc:
            return BrowserOutcome.failed(f"could not reload the page: {exc}")

    def find(self, *, text: str = "", regex: str = "") -> BrowserOutcome:
        import re as _re

        page_outcome = self._observe()
        if not page_outcome.success:
            return page_outcome
        page = page_outcome.page
        matches: list[str] = []
        if text:
            needle = text.casefold()
            matches = [
                line for line in page.text.splitlines() if needle in line.casefold()
            ]
        elif regex:
            try:
                pattern = _re.compile(regex)
            except _re.error as exc:
                return BrowserOutcome.failed(f"invalid search pattern: {exc}")
            matches = [line for line in page.text.splitlines() if pattern.search(line)]
        if not matches:
            return BrowserOutcome.failed(
                f"no page content matched {'/'.join(filter(None, [text, regex]))!r}"
            )
        found = BrowserPage(
            url=page.url,
            title=page.title,
            text="\n".join(matches[:50]),
            elements=page.elements,
            refs=page.refs,
        )
        return BrowserOutcome.ok(found, message=f"{len(matches)} match(es) found")

    def wait_for(self, *, text: str = "", time: float | None = None) -> BrowserOutcome:
        def _wait() -> BrowserOutcome:
            page = self._require_page()
            if text:
                page.wait_for_selector(f"text={text}", timeout=self._timeout_ms)
            if time:
                page.wait_for_timeout(int(min(max(time, 0), 30) * 1000))
            return BrowserOutcome.ok(self._page_model(page), message="Wait completed")

        try:
            return self._worker.submit(_wait)
        except Exception as exc:
            return BrowserOutcome.failed(f"timed out waiting: {exc}")

    def scroll(self, direction: str) -> BrowserOutcome:
        def _scroll() -> BrowserOutcome:
            page = self._require_page()
            moves = {
                "down": ("window.scrollBy(0, window.innerHeight * 0.9)", "down"),
                "up": ("window.scrollBy(0, -window.innerHeight * 0.9)", "up"),
                "right": ("window.scrollBy(window.innerWidth * 0.9, 0)", "right"),
                "left": ("window.scrollBy(-window.innerWidth * 0.9, 0)", "left"),
                "top": ("window.scrollTo(0, 0)", "top"),
                "bottom": ("window.scrollTo(0, document.body.scrollHeight)", "bottom"),
            }
            script, label = moves.get(direction, ("", ""))
            if not script:
                return BrowserOutcome.failed(f"unsupported scroll direction '{direction}'")
            page.evaluate(script)
            page.wait_for_timeout(150)
            return BrowserOutcome.ok(self._page_model(page), message=f"Scrolled {label}")

        try:
            return self._worker.submit(_scroll)
        except Exception as exc:
            return BrowserOutcome.failed(f"could not scroll: {exc}")

    # ── interaction ──

    def _locator(self, page: Any, params: dict[str, Any]) -> Any:
        """Resolve a target reference into a Playwright locator."""
        ref = str(params.get("ref") or "").strip()
        if ref:
            selector = f'[data-mamba-ref="{ref}"]'
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator.first
        role = str(params.get("role") or "").strip()
        name = str(params.get("name") or "").strip()
        text = str(params.get("text_target") or "").strip()
        if role and name:
            from playwright.sync_api import Error as PlaywrightError

            candidate = page.get_by_role(role, name=name, exact=False)
            try:
                if candidate.count() >= 1:
                    return candidate.first
            except PlaywrightError:
                pass
        if name or text:
            candidate = page.get_by_text(name or text, exact=False)
            if candidate.count() >= 1:
                return candidate.first
        if role:
            candidate = page.get_by_role(role)
            try:
                if candidate.count() == 1:
                    return candidate.first
            except Exception:
                pass
        raise BrowserTargetError(
            "could not resolve the target element on the page; refusing to act blind"
        )

    def act(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        def _act() -> BrowserOutcome:
            page = self._require_page()
            locator = self._locator(page, params)
            if action == "click":
                locator.click()
            elif action == "type":
                text = str(params.get("text") or "")
                locator.fill(text)
                if params.get("submit"):
                    locator.press("Enter")
            elif action == "clear":
                locator.fill("")
            elif action == "press_key":
                key = str(params.get("key") or "")
                if not key:
                    return BrowserOutcome.failed("a key name is required")
                page.keyboard.press(key)
            elif action == "select_option":
                values = [str(v) for v in (params.get("values") or [])]
                if not values:
                    return BrowserOutcome.failed("a value to select is required")
                locator.select_option(values)
            elif action == "hover":
                locator.hover()
            else:
                return BrowserOutcome.failed(f"unsupported browser action '{action}'")
            page.wait_for_timeout(250)
            return BrowserOutcome.ok(self._page_model(page), message=f"{action} performed")

        try:
            return self._worker.submit(_act)
        except Exception as exc:
            return BrowserOutcome.failed(f"{action} failed: {exc}")

    def close_page(self) -> BrowserOutcome:
        def _close() -> BrowserOutcome:
            page = self._require_page()
            page.close()
            return BrowserOutcome.ok(BrowserPage(), message="Page closed")

        try:
            return self._worker.submit(_close)
        except Exception as exc:
            return BrowserOutcome.failed(f"could not close the page: {exc}")


__all__ = ["PlaywrightProvider", "INTERACTIVE_ROLES"]
