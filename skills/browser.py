"""Browser skills and task handler for Mamba.

One skill and one task handler cover the whole browser capability: the task
handler maps a planner intent to a concrete browser action, resolves the
authoritative risk metadata through the tool layer, and returns the tool's
structured observation unchanged so Mamba's existing verification step can check
the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.browser.session import BrowserSession, create_default_provider
from tools.browser.tool import (
    BROWSER_OPERATIONS,
    BROWSER_TOOL_NAME,
    BrowserTool,
    BrowserToolHandler,
    browser_operation_metadata,
)
from tools.browser.types import BrowserAction, BrowserProvider
from tools.protocols import ToolExecutor
from tools.tool import StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

# ── intent mapping ──────────────────────────────────────────────────────────

_OPEN_INTENTS = frozenset({"open_url_in_browser", "open_page", "browse_url", "visit_page"})
NAVIGATE_INTENTS = frozenset({"navigate_browser", "browser_navigate", "go_to_url"})
BACK_INTENTS = frozenset({"browser_back", "go_back", "navigate_back"})
FORWARD_INTENTS = frozenset({"browser_forward", "go_forward", "navigate_forward"})
RELOAD_INTENTS = frozenset({"browser_reload", "reload_page", "refresh_page"})
CURRENT_INTENTS = frozenset({"browser_current", "get_current_page", "current_url"})
INSPECT_INTENTS = frozenset(
    {"inspect_page", "browser_inspect", "browser_inspect_page", "page_snapshot", "browser_snapshot"}
)
PAGE_TEXT_INTENTS = frozenset({"read_page", "browser_read_page", "page_text", "read_webpage"})
PAGE_LINKS_INTENTS = frozenset({"browser_links", "page_links", "list_page_links"})
PAGE_BUTTONS_INTENTS = frozenset({"browser_buttons", "page_buttons"})
PAGE_FIELDS_INTENTS = frozenset({"browser_fields", "page_fields", "form_fields"})
FIND_INTENTS = frozenset({"find_on_page", "browser_find", "search_page"})
WAIT_INTENTS = frozenset({"browser_wait", "wait_for_page", "wait_for_text"})
TARGETS_INTENTS = frozenset(
    {"list_browser_targets", "browser_targets", "list_browser_tabs", "browser_tabs"}
)
ATTACH_INTENTS = frozenset({"attach_browser", "bind_browser", "select_browser_tab"})
SCROLL_INTENTS = frozenset({"browser_scroll", "scroll_page", "scroll"})
CLICK_INTENTS = frozenset({"click_element", "browser_click", "click_on_page", "click_link"})
TYPE_INTENTS = frozenset({"type_text_in_page", "browser_type", "fill_field", "enter_text_in_page"})
CLEAR_INTENTS = frozenset({"clear_field", "browser_clear", "clear_input"})
KEY_INTENTS = frozenset({"press_key_in_page", "browser_press_key", "page_press_key"})
SELECT_INTENTS = frozenset({"select_option", "browser_select", "choose_option"})

_ALL_BROWSER_INTENTS: frozenset[str] = frozenset().union(
    _OPEN_INTENTS,
    NAVIGATE_INTENTS,
    BACK_INTENTS,
    FORWARD_INTENTS,
    RELOAD_INTENTS,
    CURRENT_INTENTS,
    INSPECT_INTENTS,
    PAGE_TEXT_INTENTS,
    PAGE_LINKS_INTENTS,
    PAGE_BUTTONS_INTENTS,
    PAGE_FIELDS_INTENTS,
    FIND_INTENTS,
    WAIT_INTENTS,
    TARGETS_INTENTS,
    ATTACH_INTENTS,
    SCROLL_INTENTS,
    CLICK_INTENTS,
    TYPE_INTENTS,
    CLEAR_INTENTS,
    KEY_INTENTS,
    SELECT_INTENTS,
)

#: Intent -> concrete browser action.
_INTENT_ACTION: dict[str, str] = {}
for _intents, _action in (
    (_OPEN_INTENTS, BrowserAction.OPEN.value),
    (NAVIGATE_INTENTS, BrowserAction.NAVIGATE.value),
    (BACK_INTENTS, BrowserAction.BACK.value),
    (FORWARD_INTENTS, BrowserAction.FORWARD.value),
    (RELOAD_INTENTS, BrowserAction.RELOAD.value),
    (CURRENT_INTENTS, BrowserAction.GET_CURRENT.value),
    (INSPECT_INTENTS, BrowserAction.INSPECT_PAGE.value),
    (PAGE_TEXT_INTENTS, BrowserAction.PAGE_TEXT.value),
    (PAGE_LINKS_INTENTS, BrowserAction.PAGE_LINKS.value),
    (PAGE_BUTTONS_INTENTS, BrowserAction.PAGE_BUTTONS.value),
    (PAGE_FIELDS_INTENTS, BrowserAction.PAGE_FIELDS.value),
    (FIND_INTENTS, BrowserAction.FIND_TEXT.value),
    (WAIT_INTENTS, BrowserAction.WAIT_FOR_TEXT.value),
    (TARGETS_INTENTS, BrowserAction.LIST_TARGETS.value),
    (ATTACH_INTENTS, BrowserAction.ATTACH.value),
    (SCROLL_INTENTS, BrowserAction.SCROLL.value),
    (CLICK_INTENTS, BrowserAction.CLICK.value),
    (TYPE_INTENTS, BrowserAction.TYPE.value),
    (CLEAR_INTENTS, BrowserAction.CLEAR.value),
    (KEY_INTENTS, BrowserAction.PRESS_KEY.value),
    (SELECT_INTENTS, BrowserAction.SELECT_OPTION.value),
):
    for _intent in _intents:
        _INTENT_ACTION[_intent] = _action


class BrowserSkill(BaseSkill):
    """One reusable Mamba capability: control and observe a browser page."""

    def __init__(
        self,
        tool: BrowserTool | None = None,
        executor: ToolExecutor | None = None,
        *,
        session: BrowserSession | None = None,
        provider: BrowserProvider | None = None,
        action: str = BrowserAction.INSPECT_PAGE.value,
    ) -> None:
        definition = BROWSER_OPERATIONS.get(action) or BROWSER_OPERATIONS[
            BrowserAction.INSPECT_PAGE.value
        ]
        super().__init__(
            Skill(
                name=definition.name,
                description=definition.description,
                metadata=definition.to_metadata(),
            )
        )
        self._action = action
        if tool is None:
            if session is None:
                session = BrowserSession(provider=provider or create_default_provider())
            elif provider is not None:
                session = BrowserSession(provider=provider)
            tool = BrowserTool(handler=BrowserToolHandler(session=session))
        self._tool = tool
        self._executor = executor or StandardToolExecutor()

    @property
    def action(self) -> str:
        return self._action

    @property
    def tool(self) -> BrowserTool:
        return self._tool

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = dict(input.task_input.step_metadata)
        action = str(meta.get("action") or meta.get("browser_action") or "").strip().lower()
        if action not in BROWSER_OPERATIONS:
            action = self._action
        meta["action"] = action
        tool_input = ToolInput(arguments=dict(meta), metadata=dict(meta))
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


@dataclass(slots=True)
class BrowserTaskHandler:
    """Dispatches browser intents to the single browser capability."""

    skill: BrowserSkill | None = None
    session: BrowserSession | None = None
    provider: BrowserProvider | None = None
    default_action: str = BrowserAction.INSPECT_PAGE.value

    def __post_init__(self) -> None:
        if self.skill is None:
            self.skill = BrowserSkill(session=self.session, provider=self.provider)

    @property
    def browser_session(self) -> BrowserSession:
        assert self.skill is not None
        return self.skill.tool.browser_handler.session

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Authoritative browser metadata, including consequential escalation."""
        params = self._params(task_input)
        return browser_operation_metadata(params)

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        params = self._params(task_input)
        assert self.skill is not None
        # The single skill serves every browser action; the action for this step
        # travels in the step metadata (already set by _params), so no skill state
        # is mutated and concurrent steps cannot interfere.
        skill_input = SkillInput.from_task(
            TaskInput(
                step_id=task_input.step_id,
                description=task_input.description,
                intent=task_input.intent,
                execution_id=task_input.execution_id,
                goal=task_input.goal,
                step_metadata=params,
            ),
            context,
        )
        return self.skill.run(skill_input).to_task_output()

    # ── helpers ──

    def _params(self, task_input: TaskInput) -> dict[str, Any]:
        """Resolve the concrete browser action for a step's intent."""
        meta = dict(task_input.step_metadata)
        intent = (task_input.intent or "").strip().lower()
        declared = str(meta.get("action") or meta.get("browser_action") or "").strip().lower()

        if declared in BROWSER_OPERATIONS:
            meta["action"] = declared
        else:
            meta["action"] = _INTENT_ACTION.get(intent, self.default_action)

        if not meta.get("description"):
            meta["description"] = task_input.description
        return meta


__all__ = [
    "BrowserSkill",
    "BrowserTaskHandler",
    "browser_intents",
]


def browser_intents() -> frozenset[str]:
    """Every planner intent the browser capability handles."""
    return _ALL_BROWSER_INTENTS
