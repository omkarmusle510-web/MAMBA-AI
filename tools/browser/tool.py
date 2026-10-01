"""Browser capability: operation metadata and the single browser tool.

Every browser action flows through one tool so the capability has a small,
auditable surface. Risk is classified per action by
:func:`browser_operation_metadata`, which is the authoritative metadata Mamba's
existing permission policy reads — there is no browser-specific permission
system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from permissions.types import RiskLevel
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .session import BrowserSession, create_default_provider
from .types import (
    CONSEQUENTIAL_CAPABLE_ACTIONS,
    READ_ONLY_ACTIONS,
    BrowserAction,
    BrowserElement,
    BrowserElementError,
    BrowserOutcome,
    BrowserPage,
    BrowserProvider,
    BrowserTargetError,
    SCROLL_DIRECTIONS,
)

BROWSER_TOOL_NAME = "browser_action"
"""The single tool name the browser capability exposes."""

#: Roles an action can legitimately use when a semantic match is ambiguous, so a
#: request like "the Search box" resolves to the field rather than the button
#: that happens to share its label.
_PREFERRED_ROLES: dict[str, tuple[str, ...]] = {
    "type": ("textbox", "searchbox", "combobox", "spinbutton", "textfield"),
    "clear": ("textbox", "searchbox", "combobox", "spinbutton", "textfield"),
    "select_option": ("combobox", "listbox"),
    "click": ("button", "link", "menuitem", "tab", "checkbox", "radio", "switch", "option"),
}

BROWSER_CAPABILITY_ID = "browser"

#: Planner intent -> concrete browser action. Kept here (next to the action
#: table) so both the tool and the skill layer resolve intents identically.
BROWSER_INTENT_ACTIONS: dict[str, str] = {
    # navigation
    "open_url_in_browser": "open",
    "open_page": "open",
    "browse_url": "open",
    "visit_page": "open",
    "navigate_browser": "navigate",
    "browser_navigate": "navigate",
    "go_to_url": "navigate",
    "browser_back": "back",
    "go_back": "back",
    "navigate_back": "back",
    "browser_forward": "forward",
    "go_forward": "forward",
    "navigate_forward": "forward",
    "browser_reload": "reload",
    "reload_page": "reload",
    "refresh_page": "reload",
    # inspection
    "browser_current": "get_current",
    "get_current_page": "get_current",
    "current_url": "get_current",
    "inspect_page": "inspect_page",
    "browser_inspect": "inspect_page",
    "browser_inspect_page": "inspect_page",
    "page_snapshot": "snapshot",
    "browser_snapshot": "snapshot",
    "read_page": "page_text",
    "browser_read_page": "page_text",
    "page_text": "page_text",
    "read_webpage": "page_text",
    "browser_links": "page_links",
    "page_links": "page_links",
    "list_page_links": "page_links",
    "browser_buttons": "page_buttons",
    "page_buttons": "page_buttons",
    "browser_fields": "page_fields",
    "page_fields": "page_fields",
    "form_fields": "page_fields",
    "find_on_page": "find_text",
    "browser_find": "find_text",
    "search_page": "find_text",
    "browser_wait": "wait_for_text",
    "wait_for_page": "wait_for_text",
    "wait_for_text": "wait_for_text",
    # targets
    "list_browser_targets": "list_targets",
    "browser_targets": "list_targets",
    "list_browser_tabs": "list_targets",
    "browser_tabs": "list_targets",
    "attach_browser": "attach",
    "bind_browser": "attach",
    "select_browser_tab": "attach",
    # interaction
    "browser_scroll": "scroll",
    "scroll_page": "scroll",
    "click_element": "click",
    "browser_click": "click",
    "click_on_page": "click",
    "click_link": "click",
    "type_text_in_page": "type",
    "browser_type": "type",
    "fill_field": "type",
    "enter_text_in_page": "type",
    "clear_field": "clear",
    "browser_clear": "clear",
    "clear_input": "clear",
    "press_key_in_page": "press_key",
    "browser_press_key": "press_key",
    "page_press_key": "press_key",
    "select_option": "select_option",
    "browser_select": "select_option",
    "choose_option": "select_option",
}


@dataclass(frozen=True, slots=True)
class BrowserOperationDefinition:
    """Security and descriptive metadata for one browser action."""

    name: str
    description: str
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    irreversible: bool = False
    user_sensitive: bool = False
    externally_visible: bool = False
    read_only: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "tool_name": BROWSER_TOOL_NAME,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "irreversible": self.irreversible,
            "user_sensitive": self.user_sensitive,
            "externally_visible": self.externally_visible,
            "read_only": self.read_only,
        }


_READ = dict(risk_level=RiskLevel.LOW, read_only=True)
_NAVIGATE = dict(risk_level=RiskLevel.LOW)
_INTERACT = dict(risk_level=RiskLevel.MEDIUM, externally_visible=True)

BROWSER_OPERATIONS: dict[str, BrowserOperationDefinition] = {
    # ── navigation (safe) ──
    BrowserAction.OPEN.value: BrowserOperationDefinition(
        name=BrowserAction.OPEN.value,
        description="Open a URL in the controlled browser and bind the page that loads.",
        **_NAVIGATE,
    ),
    BrowserAction.NAVIGATE.value: BrowserOperationDefinition(
        name=BrowserAction.NAVIGATE.value,
        description="Navigate the bound page to another URL.",
        **_NAVIGATE,
    ),
    BrowserAction.BACK.value: BrowserOperationDefinition(
        name=BrowserAction.BACK.value,
        description="Go back in the bound page's history.",
        **_NAVIGATE,
    ),
    BrowserAction.FORWARD.value: BrowserOperationDefinition(
        name=BrowserAction.FORWARD.value,
        description="Go forward in the bound page's history.",
        **_NAVIGATE,
    ),
    BrowserAction.RELOAD.value: BrowserOperationDefinition(
        name=BrowserAction.RELOAD.value,
        description="Reload the bound page.",
        **_NAVIGATE,
    ),
    # ── inspection (safe, read-only) ──
    BrowserAction.GET_CURRENT.value: BrowserOperationDefinition(
        name=BrowserAction.GET_CURRENT.value,
        description="Report the bound page's current URL and title.",
        **_READ,
    ),
    BrowserAction.INSPECT_PAGE.value: BrowserOperationDefinition(
        name=BrowserAction.INSPECT_PAGE.value,
        description=(
            "Inspect the bound page: URL, title, visible text, and the interactive "
            "elements (links, buttons, fields) available to act on."
        ),
        **_READ,
    ),
    BrowserAction.SNAPSHOT.value: BrowserOperationDefinition(
        name=BrowserAction.SNAPSHOT.value,
        description="Read the bound page's accessibility structure.",
        **_READ,
    ),
    BrowserAction.PAGE_TEXT.value: BrowserOperationDefinition(
        name=BrowserAction.PAGE_TEXT.value,
        description="Read the bound page's visible text.",
        **_READ,
    ),
    BrowserAction.PAGE_LINKS.value: BrowserOperationDefinition(
        name=BrowserAction.PAGE_LINKS.value,
        description="List the links on the bound page.",
        **_READ,
    ),
    BrowserAction.PAGE_BUTTONS.value: BrowserOperationDefinition(
        name=BrowserAction.PAGE_BUTTONS.value,
        description="List the buttons and controls on the bound page.",
        **_READ,
    ),
    BrowserAction.PAGE_FIELDS.value: BrowserOperationDefinition(
        name=BrowserAction.PAGE_FIELDS.value,
        description="List the input fields on the bound page.",
        **_READ,
    ),
    BrowserAction.FIND_TEXT.value: BrowserOperationDefinition(
        name=BrowserAction.FIND_TEXT.value,
        description="Search the bound page's content for text or a pattern.",
        **_READ,
    ),
    BrowserAction.WAIT_FOR_TEXT.value: BrowserOperationDefinition(
        name=BrowserAction.WAIT_FOR_TEXT.value,
        description="Wait for text to appear on the bound page, then observe it.",
        **_READ,
    ),
    BrowserAction.WAIT_FOR_ELEMENT.value: BrowserOperationDefinition(
        name=BrowserAction.WAIT_FOR_ELEMENT.value,
        description="Wait for an element to appear on the bound page, then observe it.",
        **_READ,
    ),
    BrowserAction.LIST_TARGETS.value: BrowserOperationDefinition(
        name=BrowserAction.LIST_TARGETS.value,
        description="List the open browser pages that can be bound as a target.",
        **_READ,
    ),
    BrowserAction.ATTACH.value: BrowserOperationDefinition(
        name=BrowserAction.ATTACH.value,
        description="Bind an already-open browser page as the target for later actions.",
        **_READ,
    ),
    # ── interaction (page/account state can change; always policy-evaluated) ──
    BrowserAction.CLICK.value: BrowserOperationDefinition(
        name=BrowserAction.CLICK.value,
        description=(
            "Click an element identified by role, accessible name, label, or text "
            "reference. Refuses to click when the element cannot be identified."
        ),
        **_INTERACT,
    ),
    BrowserAction.TYPE.value: BrowserOperationDefinition(
        name=BrowserAction.TYPE.value,
        description="Type text into an identified input field on the bound page.",
        **_INTERACT,
    ),
    BrowserAction.CLEAR.value: BrowserOperationDefinition(
        name=BrowserAction.CLEAR.value,
        description="Clear the contents of an identified input field.",
        **_INTERACT,
    ),
    BrowserAction.PRESS_KEY.value: BrowserOperationDefinition(
        name=BrowserAction.PRESS_KEY.value,
        description="Press a keyboard key on the bound page.",
        **_INTERACT,
    ),
    BrowserAction.SCROLL.value: BrowserOperationDefinition(
        name=BrowserAction.SCROLL.value,
        description="Scroll the bound page.",
        **_INTERACT,
    ),
    BrowserAction.SELECT_OPTION.value: BrowserOperationDefinition(
        name=BrowserAction.SELECT_OPTION.value,
        description="Select one or more values in an identified dropdown.",
        **_INTERACT,
    ),
    BrowserAction.HOVER.value: BrowserOperationDefinition(
        name=BrowserAction.HOVER.value,
        description="Hover over an identified element.",
        **_INTERACT,
    ),
}

#: Words that mark a step as reaching the outside world (submitting, posting,
#: sending, purchasing, deleting, changing account state).
_CONSEQUENTIAL_TOKENS: frozenset[str] = frozenset(    {
        "send",
        "submit",
        "post",
        "publish",
        "purchase",
        "buy",
        "pay",
        "checkout",
        "order",
        "delete",
        "remove",
        "unsubscribe",
        "confirm",
        "accept",
        "sign up",
        "signup",
        "register",
        "subscribe",
        "follow",
        "like",
        "comment",
        "reply",
        "apply",
        "book",
        "reserve",
        "invite",
        "share",
        "upload",
        "change password",
        "change settings",
        "account",
        "transfer",
    }
)


def is_consequential(params: dict[str, Any]) -> bool:
    """Whether a step describes an action that reaches the outside world.

    The planner (or the user) can state this explicitly via
    ``consequential: true`` / ``public: true`` / ``safety: "consequential"``, and
    a conservative keyword scan of the step's own description is used as a
    secondary signal so a plainly consequential click (e.g. "click Send") is not
    silently treated as an ordinary page interaction.
    """
    for key in ("consequential", "public", "side_effecting", "external_action"):
        if params.get(key) is True:
            return True
    safety = str(params.get("safety") or "").strip().lower()
    if safety in ("consequential", "high", "public", "external"):
        return True

    haystack = " ".join(
        str(params.get(key) or "")
        for key in ("description", "element", "name", "label", "intent_description", "goal")
    ).casefold()
    return any(token in haystack for token in _CONSEQUENTIAL_TOKENS)


def resolve_browser_action(params: dict[str, Any]) -> str:
    """Resolve the concrete browser action for a step.

    Accepts the canonical action name (``click``), the planner intent that maps
    to it (``click_element``), or a browser-flavoured alias. Returns "" when
    nothing recognisable was supplied.
    """
    raw = str(
        params.get("action")
        or params.get("browser_action")
        or params.get("browser_operation")
        or ""
    ).strip().lower()
    if raw in BROWSER_OPERATIONS:
        return raw
    if raw in BROWSER_INTENT_ACTIONS:
        return BROWSER_INTENT_ACTIONS[raw]
    return ""


def browser_operation_metadata(params: dict[str, Any]) -> dict[str, Any]:
    """Authoritative metadata for a browser action, including escalation.

    Read-only and navigation actions stay LOW risk. Interactions are MEDIUM. An
    action that submits/posts/sends/purchases/deletes/alters account state is
    escalated to HIGH with ``irreversible`` and ``externally_visible`` set, which
    routes it through Mamba's existing approval flow (HIGH → ASK) — no separate
    browser permission mechanism is introduced, and nothing consequential is
    auto-authorized.
    """
    action = resolve_browser_action(params)
    definition = BROWSER_OPERATIONS.get(action)
    if definition is None:
        return {
            "action": action or str(params.get("action") or "browser_action"),
            "tool_name": BROWSER_TOOL_NAME,
            "risk_level": RiskLevel.MEDIUM,
            "destructive": False,
            "irreversible": False,
            "user_sensitive": False,
            "externally_visible": True,
            "read_only": False,
        }

    metadata = definition.to_metadata()
    metadata["browser_action"] = action

    consequential = is_consequential(params)
    metadata["consequential"] = consequential
    if consequential and action in CONSEQUENTIAL_CAPABLE_ACTIONS:
        metadata["risk_level"] = RiskLevel.HIGH
        metadata["irreversible"] = True
        metadata["externally_visible"] = True
        metadata["escalation_reason"] = (
            "this browser action can submit, post, send, purchase, delete, or change "
            "account state, so it requires explicit user approval"
        )
    return metadata


def _with_binding(outcome: BrowserOutcome, binding: Any) -> BrowserOutcome:
    """Attach a target binding to an outcome's metadata (frozen safe)."""
    from dataclasses import replace as _replace

    if binding is None:
        return outcome
    return _replace(
        outcome,
        metadata={**dict(outcome.metadata), **binding.to_metadata()},
    )


def _element_metadata(page: BrowserPage, element: BrowserElement | None) -> dict[str, Any]:
    refs = dict(page.refs) or {item.ref: item.describe() for item in page.elements}
    metadata: dict[str, Any] = {
        "element_refs": refs,
        "element_count": len(page.elements),
    }
    if page.url:
        metadata["page_url"] = page.url
    if page.title:
        metadata["page_title"] = page.title
    if page.text:
        metadata["page_text"] = page.text
    if element is not None:
        metadata["target_element"] = element.describe()
        metadata["target_ref"] = element.ref
        metadata["target_role"] = element.role
        metadata["target_name"] = element.name
    return metadata


class BrowserToolHandler:
    """Executes one browser action through a bound browser session."""

    def __init__(
        self,
        session: BrowserSession | None = None,
        *,
        provider: BrowserProvider | None = None,
    ) -> None:
        if session is None:
            session = BrowserSession(provider=provider or create_default_provider())
        self._session = session
    @property
    def session(self) -> BrowserSession:
        return self._session

    # ── availability ──

    def is_available(self) -> tuple[bool, str]:
        return self._session.is_available()

    def get_metadata(self, params: dict[str, Any]) -> dict[str, Any]:
        return browser_operation_metadata(params)

    # ── execution ──

    def run(self, input: ToolInput) -> ToolOutput:
        params = {**dict(input.metadata), **dict(input.arguments)}
        action = resolve_browser_action(params)
        if not action:
            supplied = params.get("action") or params.get("browser_action")
            return ToolOutput(
                success=False,
                error=(
                    f"unsupported browser action '{supplied}'"
                    if supplied
                    else "no browser action was specified"
                ),
                metadata={"error": "missing_action"},
            )
        params["action"] = action

        available, reason = self._session.is_available()
        if not available:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={
                    **browser_operation_metadata(params),
                    "available": False,
                    "error": "browser_unavailable",
                },
            )

        try:
            outcome = self._dispatch(action, params)
        except BrowserTargetError as exc:
            return self._failure(action, params, str(exc), "target_error")
        except BrowserElementError as exc:
            return self._failure(action, params, str(exc), "element_error")
        except Exception as exc:
            return self._failure(action, params, f"{action} failed: {exc}", "execution_error")

        metadata = {
            **browser_operation_metadata(params),
            **(outcome.metadata or {}),
        }

        if not outcome.success:
            metadata["error"] = "browser_action_failed"
            binding = self._session.binding
            if binding is not None:
                metadata.update(binding.to_metadata())
            metadata.update(_element_metadata(outcome.page, None))
            return ToolOutput(
                success=False,
                error=outcome.error or f"{action} failed",
                metadata=metadata,
            )

        page = outcome.page
        binding = self._session.binding
        if binding is not None:
            metadata.update(binding.to_metadata())
        metadata.update(_element_metadata(page, outcome.metadata.get("_element") if isinstance(outcome.metadata, dict) else None))
        # The observable page state is also exposed as `actual`, so Mamba's
        # existing verifier can check a step's `expected` predicate against what
        # the page actually shows (rather than trusting that the action ran).
        metadata["actual"] = page.observation_text()
        metadata["observed_page"] = page.observables()

        return ToolOutput(
            success=True,
            result=self._render(action, params, outcome, page),
            metadata=metadata,
        )

    def _failure(self, action: str, params: dict[str, Any], message: str, code: str) -> ToolOutput:
        return ToolOutput(
            success=False,
            error=message,
            metadata={
                **browser_operation_metadata(params),
                "error": code,
                "browser_action": action,
            },
        )

    # ── dispatch ──

    def _dispatch(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        if action in (BrowserAction.OPEN.value, BrowserAction.NAVIGATE.value):
            url = self._requested_url(params)
            if not url:
                return BrowserOutcome.failed(
                    "no URL was provided to open", error_code="missing_url"
                )
            return self._session.navigate(url)

        if action == BrowserAction.BACK.value:
            return self._session.navigate_history("back")
        if action == BrowserAction.FORWARD.value:
            return self._session.navigate_history("forward")
        if action == BrowserAction.RELOAD.value:
            return self._session.reload()

        if action == BrowserAction.LIST_TARGETS.value:
            return self._list_targets()

        if action == BrowserAction.ATTACH.value:
            return self._attach(params)

        if action in (
            BrowserAction.GET_CURRENT.value,
            BrowserAction.INSPECT_PAGE.value,
            BrowserAction.SNAPSHOT.value,
            BrowserAction.PAGE_TEXT.value,
            BrowserAction.PAGE_LINKS.value,
            BrowserAction.PAGE_BUTTONS.value,
            BrowserAction.PAGE_FIELDS.value,
        ):
            return self._inspect(action, params)

        if action == BrowserAction.FIND_TEXT.value:
            return self._session.find(
                text=str(params.get("query") or params.get("text") or ""),
                regex=str(params.get("regex") or ""),
            )

        if action in (BrowserAction.WAIT_FOR_TEXT.value, BrowserAction.WAIT_FOR_ELEMENT.value):
            text = str(params.get("text") or params.get("query") or "")
            seconds = params.get("seconds") or params.get("time")
            try:
                time_value = float(seconds) if seconds is not None else None
            except (TypeError, ValueError):
                time_value = None
            return self._session.wait_for(text=text, time_seconds=time_value)

        if action == BrowserAction.SCROLL.value:
            direction = str(params.get("direction") or "down").strip().lower()
            if direction not in SCROLL_DIRECTIONS:
                return BrowserOutcome.failed(
                    f"unsupported scroll direction '{direction}'"
                )
            return self._session.scroll(direction)

        if action in (
            BrowserAction.CLICK.value,
            BrowserAction.TYPE.value,
            BrowserAction.CLEAR.value,
            BrowserAction.SELECT_OPTION.value,
            BrowserAction.HOVER.value,
        ):
            return self._interact(action, params)

        if action == BrowserAction.PRESS_KEY.value:
            key = str(params.get("key") or "").strip()
            if not key:
                return BrowserOutcome.failed("no key was provided to press")
            return self._session.act("press_key", {"key": key})

        return BrowserOutcome.failed(f"unsupported browser action '{action}'")

    # ── helpers ──

    @staticmethod
    def _requested_url(params: dict[str, Any]) -> str:
        raw = str(
            params.get("url")
            or params.get("target_url")
            or params.get("href")
            or params.get("site")
            or ""
        ).strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://", "file://", "about:")):
            return raw
        if raw.startswith("//"):
            return f"https:{raw}"
        return f"https://{raw}"

    def _list_targets(self) -> BrowserOutcome:
        targets = self._session.targets()
        text = self._session.describe_targets()
        page = BrowserPage(
            url=targets[0].url if targets else "",
            title=targets[0].title if targets else "",
            text=text,
        )
        return BrowserOutcome.ok(
            page,
            message=f"{len(targets)} browser page(s) open",
            targets=[target.to_dict() for target in targets],
        )

    def _attach(self, params: dict[str, Any]) -> BrowserOutcome:
        requested = params.get("tab_index")
        index = None
        if requested is not None and str(requested).strip() != "":
            try:
                index = int(requested)
            except (TypeError, ValueError):
                return BrowserOutcome.failed(f"invalid tab index '{requested}'")
        binding = self._session.bind(
            url=str(params.get("url") or params.get("bound_url") or ""),
            title=str(params.get("title") or params.get("bound_title") or ""),
            tab_index=index,
            session_id=str(params.get("session_id") or ""),
        )
        binding = self._session.attach(binding)
        observed = self._session.observe()
        page = observed.page if observed.success else BrowserPage(url=binding.url, title=binding.title)
        placed = BrowserOutcome.ok(
            page,
            message=f"Bound browser tab {binding.tab_index} ({binding.url})",
            attached=True,
        )
        return _with_binding(placed, binding)

    def _binding_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Target-binding parameters for actions that need a bound page."""
        return {
            "session_id": params.get("session_id"),
            "bound_url": params.get("bound_url"),
            "tab_index": params.get("tab_index"),
        }

    def _ensure_binding(self, params: dict[str, Any], *, need_page: bool) -> None:
        """Resolve (or re-verify) the page this action is allowed to touch.

        A step that carries a bound page keeps it; otherwise the target is
        resolved from the step's own request (tab index, URL, title) or, when
        nothing was named and only one page is open, that page. Ambiguity and
        mismatch both raise, so an action is never taken against an unbound or
        drifted page.
        """
        explicit_tab = params.get("tab_index")
        index: int | None = None
        if explicit_tab is not None and str(explicit_tab).strip() != "":
            try:
                index = int(explicit_tab)
            except (TypeError, ValueError):
                raise BrowserTargetError(f"invalid tab index '{explicit_tab}'") from None

        if index is not None:
            self._session.select_tab(index)
            self._session.binding = self._session.bind(
                url=str(params.get("bound_url") or ""),
                title=str(params.get("bound_title") or ""),
                tab_index=index,
                session_id=str(params.get("session_id") or ""),
            )
            return

        if self._session.binding is not None:
            self._session.binding = self._session.verify_still_bound(self._session.binding)
            return

        self._session.binding = self._session.bind(
            url=str(params.get("bound_url") or ""),
            title=str(params.get("bound_title") or ""),
            session_id=str(params.get("session_id") or ""),
        )

    def _inspect(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        self._ensure_binding(params, need_page=True)
        observed = self._session.observe()
        if not observed.success:
            return observed

        page = observed.page
        if action in (BrowserAction.GET_CURRENT.value,):
            return observed

        if action == BrowserAction.PAGE_TEXT.value:
            filtered = BrowserPage(url=page.url, title=page.title, text=page.text)
            return BrowserOutcome.ok(filtered, message=f"{len(page.text)} characters of page text")

        if action == BrowserAction.PAGE_LINKS.value:
            links = page.links
            rendered = "\n".join(f"- {e.describe()}" for e in links[:80]) or "No links found."
            return BrowserOutcome.ok(
                BrowserPage(url=page.url, title=page.title, text=rendered, elements=page.elements, refs=page.refs),
                message=f"{len(links)} link(s) on the page",
            )

        if action == BrowserAction.PAGE_BUTTONS.value:
            buttons = page.buttons
            rendered = "\n".join(f"- {e.describe()}" for e in buttons[:80]) or "No buttons found."
            return BrowserOutcome.ok(
                BrowserPage(url=page.url, title=page.title, text=rendered, elements=page.elements, refs=page.refs),
                message=f"{len(buttons)} button/control element(s) on the page",
            )

        if action == BrowserAction.PAGE_FIELDS.value:
            fields = page.fields
            rendered = "\n".join(f"- {e.describe()}" for e in fields[:80]) or "No input fields found."
            return BrowserOutcome.ok(
                BrowserPage(url=page.url, title=page.title, text=rendered, elements=page.elements, refs=page.refs),
                message=f"{len(fields)} input field(s) on the page",
            )

        return observed

    def _interact(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        # Target safety: the binding is re-verified before anything is resolved,
        # so a drifted page is refused rather than having a stale element used.
        self._ensure_binding(params, need_page=True)

        target: BrowserElement | None = None
        page = BrowserPage()
        observed = self._session.observe()
        if observed.success:
            page = observed.page

        ref = str(params.get("ref") or params.get("target_ref") or "").strip()
        role = str(params.get("role") or "").strip()
        # The element is identified only by what the step says about the element
        # itself. The step's prose description is a label for humans, never a
        # matching key — otherwise "click the Send button" would be searched for
        # as the element's text.
        name = str(
            params.get("name")
            or params.get("label")
            or params.get("element")
            or params.get("accessible_name")
            or ""
        ).strip()
        text = str(params.get("text") or params.get("text_target") or "").strip()
        description = str(params.get("element") or name or role or text or "").strip()
        step_description = str(params.get("description") or "").strip()

        if action == BrowserAction.TYPE.value and not params.get("text") and not text:
            return BrowserOutcome.failed("no text was provided to type")

        if ref or role or name or text:
            if not page.elements:
                # The provider gave no parsed structure; let it resolve the
                # target from the semantic parameters instead of guessing here.
                act_params = {
                    "element": description,
                    "name": name,
                    "description": step_description or description,
                }
                if ref:
                    act_params["ref"] = ref
                if role:
                    act_params["role"] = role
                if action == BrowserAction.TYPE.value:
                    act_params["text"] = str(params.get("text") or "")
                    if params.get("submit") is True:
                        act_params["submit"] = True
                elif action == BrowserAction.SELECT_OPTION.value:
                    values = params.get("values") or params.get("value") or params.get("option")
                    if isinstance(values, str):
                        values = [values]
                    if not values:
                        return BrowserOutcome.failed("no value was provided to select")
                    act_params["values"] = [str(v) for v in values]
                outcome = self._session.act(action, act_params)
                return self._after_action(action, outcome, None)
            # Resolution failures from a parsed page (ambiguous, missing, or
            # stale element) are real refusals and must surface as such.
            target = self._session.resolve_element(
                page,
                ref=ref,
                role=role,
                name=name,
                text="" if (ref or role or name) else text,
                description=description,
                prefer_roles=_PREFERRED_ROLES.get(action, ()),
            )
        else:
            raise BrowserElementError(
                "the intended element was not identified; name it by role, label, or "
                "visible text (refusing to act blind)."
            )

        act_params: dict[str, Any] = {}
        if target is not None:
            act_params["ref"] = target.ref
            act_params["element"] = target.describe()
            act_params["name"] = target.name
            act_params["description"] = step_description or target.describe()
        else:
            # The page exposed no parsed structure (or the provider resolves
            # targets itself), but the caller did name the element explicitly.
            act_params["element"] = description
            act_params["name"] = name
            act_params["description"] = step_description or description
            if ref:
                act_params["ref"] = ref
            if role:
                act_params["role"] = role

        if action == BrowserAction.TYPE.value:
            act_params["text"] = str(params.get("text") or "")
            if params.get("submit") is True:
                act_params["submit"] = True
        elif action == BrowserAction.SELECT_OPTION.value:
            values = params.get("values") or params.get("value") or params.get("option")
            if isinstance(values, str):
                values = [values]
            if not values:
                return BrowserOutcome.failed("no value was provided to select")
            act_params["values"] = [str(v) for v in values]

        outcome = self._session.act(action, act_params)
        return self._after_action(
            action, outcome, target
        )

    def _after_action(
        self,
        action: str,
        outcome: BrowserOutcome,
        target: BrowserElement | None,
    ) -> BrowserOutcome:
        """Post-process an interaction: attach the target, re-observe the page.

        An interaction can navigate, so the page is re-observed and the binding
        refreshed to the page the action actually produced.
        """
        from dataclasses import replace as _replace

        if target is not None:
            outcome = _replace(
                outcome, metadata={**dict(outcome.metadata), "_element": target}
            )
        if outcome.success:
            refreshed = self._session.observe()
            if refreshed.success:
                outcome = _replace(outcome, page=refreshed.page)
            self._session.binding = self._session.bind()
        return outcome

    # ── rendering ──

    @staticmethod
    def _render(
        action: str,
        params: dict[str, Any],
        outcome: BrowserOutcome,
        page: BrowserPage,
    ) -> str:
        header = f"{action.replace('_', ' ').capitalize()} completed."
        if page.url or page.title:
            header += f" Page: {page.title or '(untitled)'} [{page.url}]"

        if action in (BrowserAction.INSPECT_PAGE.value, BrowserAction.SNAPSHOT.value):
            return f"{header}\n\n{_render_page(page)}"
        if action in (
            BrowserAction.PAGE_TEXT.value,
            BrowserAction.PAGE_LINKS.value,
            BrowserAction.PAGE_BUTTONS.value,
            BrowserAction.PAGE_FIELDS.value,
            BrowserAction.FIND_TEXT.value,
            BrowserAction.LIST_TARGETS.value,
            BrowserAction.GET_CURRENT.value,
        ):
            return f"{header}\n{page.text}".strip()
        if outcome.message:
            return f"{header}\n{outcome.message}"
        return header


def _render_page(page: BrowserPage, *, text_limit: int = 2000, element_limit: int = 40) -> str:
    """Render a page observation for the planner / user."""
    lines: list[str] = []
    lines.append(f"URL: {page.url}")
    lines.append(f"Title: {page.title or '(untitled)'}")
    if page.text:
        text = page.text.strip()
        if len(text) > text_limit:
            text = text[:text_limit] + " …"
        lines.append("")
        lines.append("Visible text:")
        lines.append(text)
    interactive = [e for e in page.elements if e.role or e.url]
    if interactive:
        lines.append("")
        lines.append("Interactive elements:")
        for element in interactive[:element_limit]:
            lines.append(f"- [{element.ref}] {element.describe()}")
    return "\n".join(lines)


class BrowserTool(BaseTool):
    """The browser capability tool."""

    def __init__(self, handler: BrowserToolHandler | None = None) -> None:
        self._handler = handler or BrowserToolHandler()
        definition = BROWSER_OPERATIONS[BrowserAction.INSPECT_PAGE.value]
        super().__init__(
            tool=Tool(
                name=BROWSER_TOOL_NAME,
                description=(
                    "Control a real browser session: navigate, inspect pages and their "
                    "elements, click, type, scroll, and observe the result."
                ),
                metadata=definition.to_metadata(),
            ),
            handler=self._handler,
        )

    @property
    def browser_handler(self) -> BrowserToolHandler:
        return self._handler


__all__ = [
    "BROWSER_CAPABILITY_ID",
    "BROWSER_OPERATIONS",
    "BROWSER_TOOL_NAME",
    "BrowserOperationDefinition",
    "BrowserTool",
    "BrowserToolHandler",
    "browser_operation_metadata",
    "is_consequential",
]
