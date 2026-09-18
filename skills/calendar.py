"""Calendar skill and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.calendar.errors import CalendarError
from tools.calendar.providers import CalendarProvider
from tools.calendar.tool import CalendarTool
from tools.calendar.types import CALENDAR_OPERATIONS, CalendarAction
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_CALENDAR_INTENTS = frozenset(
    {
        "list_events",
        "list_calendar",
        "search_events",
        "search_calendar",
        "get_event",
        "calendar_event",
        "create_event",
        "schedule_event",
        "schedule_meeting",
        "modify_event",
        "reschedule_event",
        "reschedule_meeting",
        "cancel_event",
        "cancel_meeting",
        "delete_event",
        "check_conflicts",
        "calendar",
    }
)


def _resolve_calendar_action(intent_or_action: str) -> CalendarAction:
    cleaned = intent_or_action.strip().lower()
    if cleaned in ("list_events", "list_calendar", "list", "upcoming_events", "agenda"):
        return CalendarAction.LIST_EVENTS
    if cleaned in ("search_events", "search_calendar", "search", "find_event", "find_meeting"):
        return CalendarAction.SEARCH_EVENTS
    if cleaned in ("get_event", "calendar_event", "get"):
        return CalendarAction.GET_EVENT
    if cleaned in ("check_conflicts", "conflicts", "check_availability"):
        return CalendarAction.CHECK_CONFLICTS
    if cleaned in ("create_event", "schedule_event", "schedule_meeting", "create", "schedule"):
        return CalendarAction.CREATE_EVENT
    if cleaned in ("modify_event", "reschedule_event", "reschedule_meeting", "modify", "update", "reschedule"):
        return CalendarAction.MODIFY_EVENT
    if cleaned in ("cancel_event", "cancel_meeting", "delete_event", "cancel", "delete"):
        return CalendarAction.CANCEL_EVENT
    return CalendarAction.LIST_EVENTS


class CalendarSkill(BaseSkill):
    """Unified skill for calendar event inspection, scheduling, modification, and cancellation."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        provider: CalendarProvider | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        skill = Skill(
            name="calendar",
            description="Manage, search, create, modify, and cancel calendar events.",
            metadata={"domain": "calendar"},
        )
        super().__init__(skill)
        self._tool = tool or CalendarTool(provider=provider)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        raw_intent = str(meta.get("action") or input.task_input.intent or "").strip().lower()
        action = _resolve_calendar_action(raw_intent)

        arguments = dict(meta)
        arguments["action"] = action.value

        tool_input = ToolInput(
            arguments=arguments,
            metadata=dict(meta),
        )

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
            if not tool_output.success:
                err = tool_output.error or "Calendar operation failed"
                return SkillOutput(
                    content=str(tool_output.metadata.get("formatted") or err),
                    success=False,
                    metadata={
                        **tool_output.metadata,
                        "action": action.value,
                        "result": tool_output.result,
                        "error": err,
                    },
                )
            content_str = str(
                tool_output.metadata.get("formatted")
                or tool_output.result
                or ""
            )
            return SkillOutput(
                content=content_str,
                success=True,
                metadata={
                    **tool_output.metadata,
                    "action": action.value,
                    "result": tool_output.result,
                },
            )
        except CalendarError as exc:
            return SkillOutput(
                content=f"Calendar execution failed: {exc}",
                success=False,
                metadata={"action": action.value, "error": str(exc)},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"Calendar execution failed: {exc}",
                success=False,
                metadata={"action": action.value, "error": str(exc)},
            )


@dataclass(slots=True)
class CalendarTaskHandler:
    """Dispatches calendar task inputs to CalendarSkill with authoritative security metadata."""

    calendar_skill: CalendarSkill | None = None

    def __post_init__(self) -> None:
        if self.calendar_skill is None:
            self.calendar_skill = CalendarSkill()

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for the calendar action."""
        raw_intent = str(
            task_input.step_metadata.get("action") or task_input.intent or ""
        ).strip().lower()
        action = _resolve_calendar_action(raw_intent)
        defn = CALENDAR_OPERATIONS.get(action)
        if defn is not None:
            return defn.to_metadata()
        return CALENDAR_OPERATIONS[CalendarAction.LIST_EVENTS].to_metadata()

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        assert self.calendar_skill is not None
        return self.calendar_skill.run(skill_input).to_task_output()

