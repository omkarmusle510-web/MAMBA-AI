"""Contracts and types for Mamba's browser-interaction capability.

The capability is provider-independent: Mamba talks to a
:class:`BrowserProvider`, and a provider adapter (Playwright MCP today, anything
else later) implements it. Nothing above this layer knows which provider is in
use, and no provider is allowed to bypass Mamba's permission or verification
boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class BrowserAction(StrEnum):
    """Browser operations Mamba exposes as one capability."""

    # ── navigation ──
    OPEN = "open"
    NAVIGATE = "navigate"
    BACK = "back"
    FORWARD = "forward"
    RELOAD = "reload"
    # ── inspection ──
    GET_CURRENT = "get_current"
    PAGE_TEXT = "page_text"
    PAGE_LINKS = "page_links"
    PAGE_FIELDS = "page_fields"
    PAGE_BUTTONS = "page_buttons"
    SNAPSHOT = "snapshot"
    INSPECT_PAGE = "inspect_page"
    LIST_TARGETS = "list_targets"
    FIND_TEXT = "find_text"
    WAIT_FOR_TEXT = "wait_for_text"
    WAIT_FOR_ELEMENT = "wait_for_element"
    # ── interaction ──
    CLICK = "click"
    TYPE = "type"
    FILL = "type"
    CLEAR = "clear"
    PRESS_KEY = "press_key"
    SCROLL = "scroll"
    SELECT_OPTION = "select_option"
    HOVER = "hover"
    # ── target binding ──
    ATTACH = "attach"


#: Actions that only read the page. These never mutate page or account state.
READ_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        BrowserAction.GET_CURRENT,
        BrowserAction.PAGE_TEXT,
        BrowserAction.PAGE_LINKS,
        BrowserAction.PAGE_FIELDS,
        BrowserAction.PAGE_BUTTONS,
        BrowserAction.SNAPSHOT,
        BrowserAction.INSPECT_PAGE,
        BrowserAction.LIST_TARGETS,
        BrowserAction.FIND_TEXT,
        BrowserAction.WAIT_FOR_TEXT,
        BrowserAction.WAIT_FOR_ELEMENT,
    }
)

#: Actions that reach a page/account and can therefore be consequential.
MUTATING_ACTIONS: frozenset[str] = frozenset(
    {
        BrowserAction.CLICK,
        BrowserAction.TYPE,
        BrowserAction.CLEAR,
        BrowserAction.PRESS_KEY,
        BrowserAction.SELECT_OPTION,
        BrowserAction.HOVER,
    }
)

#: Actions that can expose something to the outside world when they submit,
#: post, purchase, delete, or change account state.
CONSEQUENTIAL_CAPABLE_ACTIONS: frozenset[str] = frozenset(
    {
        BrowserAction.CLICK,
        BrowserAction.TYPE,
        BrowserAction.PRESS_KEY,
        BrowserAction.SELECT_OPTION,
    }
)

SCROLL_DIRECTIONS: frozenset[str] = frozenset({"up", "down", "left", "right", "top", "bottom"})


@dataclass(frozen=True, slots=True)
class BrowserElement:
    """One interactive element identified from the page's accessibility tree."""

    ref: str
    role: str = ""
    name: str = ""
    url: str = ""
    element_type: str = ""
    value: str = ""
    disabled: bool = False
    context: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = [self.role or "element"]
        if self.name:
            parts.append(f'"{self.name}"')
        if self.url:
            parts.append(f"-> {self.url}")
        if self.value:
            parts.append(f"(value: {self.value})")
        return " ".join(parts)

    def matches(self, *, role: str = "", name: str = "", text: str = "") -> bool:
        """Whether this element satisfies the requested semantic identity."""
        if role and role.strip().lower() != self.role.strip().lower():
            return False
        if name and name.strip().casefold() != self.name.strip().casefold():
            return False
        if text:
            needle = text.strip().casefold()
            haystack = f"{self.name} {self.role} {self.element_type}".casefold()
            if needle not in haystack:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "url": self.url,
            "type": self.element_type,
            "value": self.value,
            "disabled": self.disabled,
        }


@dataclass(frozen=True, slots=True)
class BrowserTarget:
    """An identified browser session/page that can be bound as the target."""

    session_id: str
    tab_index: int
    url: str
    title: str
    current: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tab_index": self.tab_index,
            "url": self.url,
            "title": self.title,
            "current": self.current,
        }


@dataclass(frozen=True, slots=True)
class BrowserPage:
    """Structured view of the page after an operation.

    This is the observation payload: everything Mamba needs to decide whether the
    requested outcome actually occurred, without a second reasoning system.
    """

    url: str = ""
    title: str = ""
    text: str = ""
    elements: tuple[BrowserElement, ...] = ()
    refs: dict[str, str] = field(default_factory=dict)
    """Element reference -> human-readable description, for targeting."""

    @property
    def links(self) -> tuple[BrowserElement, ...]:
        return tuple(e for e in self.elements if e.role == "link" or e.url)

    @property
    def buttons(self) -> tuple[BrowserElement, ...]:
        return tuple(
            e
            for e in self.elements
            if e.role in ("button", "menuitem", "tab", "checkbox", "radio", "switch")
        )

    @property
    def fields(self) -> tuple[BrowserElement, ...]:
        return tuple(
            e
            for e in self.elements
            if e.role in ("textbox", "searchbox", "combobox", "listbox", "spinbutton", "slider")
            or e.element_type in ("input", "textarea", "search", "email", "password", "url", "tel")
        )

    def observables(self) -> dict[str, Any]:
        """The observable facts about this page state."""
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "element_count": len(self.elements),
        }

    def observation_text(self) -> str:
        """A single string combining the observable facts.

        Used with the existing verifier's ``contains`` predicate so browser
        outcomes reuse the standard verification path.
        """
        return f"url: {self.url}\ntitle: {self.title}\n{self.text}"


@dataclass(frozen=True, slots=True)
class BrowserOutcome:
    """Result of one provider operation."""

    success: bool
    page: BrowserPage = field(default_factory=BrowserPage)
    message: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, page: BrowserPage | None = None, message: str = "", **metadata: Any) -> "BrowserOutcome":
        return cls(success=True, page=page or BrowserPage(), message=message, metadata=metadata)

    @classmethod
    def failed(cls, error: str, **metadata: Any) -> "BrowserOutcome":
        return cls(success=False, error=error, metadata=metadata)


class BrowserProvider(Protocol):
    """A browser-control backend (Playwright MCP, or another provider)."""

    provider_name: str

    def is_available(self) -> tuple[bool, str]: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def current_page(self) -> tuple[str, str]:
        """Return (url, title) of the page the provider is currently on."""
        ...

    def list_targets(self) -> list[BrowserTarget]: ...

    def navigate(self, url: str) -> BrowserOutcome: ...

    def history(self, direction: str) -> BrowserOutcome:
        """``direction`` is ``back`` or ``forward``."""
        ...

    def reload(self) -> BrowserOutcome: ...

    def act(self, action: str, params: dict[str, Any]) -> BrowserOutcome: ...

    def snapshot(self) -> BrowserOutcome: ...

    def page_text(self) -> BrowserOutcome: ...

    def find(self, *, text: str = "", regex: str = "") -> BrowserOutcome: ...

    def wait_for(self, *, text: str = "", time: float | None = None) -> BrowserOutcome: ...


class BrowserError(RuntimeError):
    """Raised when a browser operation cannot be performed."""


class BrowserTargetError(BrowserError):
    """Raised when the intended browser target cannot be established safely."""


class BrowserElementError(BrowserError):
    """Raised when the intended element cannot be identified confidently."""


__all__ = [
    "BrowserAction",
    "BrowserElement",
    "BrowserElementError",
    "BrowserError",
    "BrowserOutcome",
    "BrowserPage",
    "BrowserProvider",
    "BrowserTarget",
    "BrowserTargetError",
    "CONSEQUENTIAL_CAPABLE_ACTIONS",
    "MUTATING_ACTIONS",
    "READ_ONLY_ACTIONS",
    "SCROLL_DIRECTIONS",
]
