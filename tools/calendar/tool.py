"""Calendar tool implementation for Mamba."""

from __future__ import annotations

from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import CalendarError, CalendarValidationError
from .providers import CalendarProvider, SimulatedCalendarProvider
from .types import CALENDAR_OPERATIONS, CalendarAction


class CalendarHandler(ToolHandler):
    """Executes calendar operations against a configured CalendarProvider."""

    def __init__(self, provider: CalendarProvider | None = None) -> None:
        self._provider = provider or SimulatedCalendarProvider()

    def run(self, input: ToolInput) -> ToolOutput:
        args = input.arguments
        action_name = str(args.get("action") or input.metadata.get("action") or "").strip().lower()

        try:
            if action_name in (CalendarAction.LIST_EVENTS.value, "list"):
                start_time = args.get("start_time")
                end_time = args.get("end_time")
                max_results = int(args.get("max_results") or 10)
                events = self._provider.list_events(
                    start_time=str(start_time) if start_time else None,
                    end_time=str(end_time) if end_time else None,
                    max_results=max_results,
                )
                dict_events = [e.to_dict() for e in events]
                lines = [
                    f"- [{e.id}] {e.title} ({e.start_time} to {e.end_time.split('T')[-1] if 'T' in e.end_time else e.end_time})"
                    for e in events
                ]
                text_content = (
                    f"Found {len(events)} upcoming event(s):\n" + "\n".join(lines)
                    if events
                    else "No upcoming events found."
                )
                return ToolOutput(
                    success=True,
                    result={"events": dict_events, "count": len(events)},
                    metadata={
                        "action": CalendarAction.LIST_EVENTS.value,
                        "count": len(events),
                        "formatted": text_content,
                    },
                )

            elif action_name in (CalendarAction.SEARCH_EVENTS.value, "search"):
                query = str(args.get("query") or args.get("q") or "").strip()
                max_results = int(args.get("max_results") or 5)
                events = self._provider.search_events(query, max_results=max_results)
                dict_events = [e.to_dict() for e in events]
                lines = [
                    f"- [{e.id}] {e.title} ({e.start_time} to {e.end_time.split('T')[-1] if 'T' in e.end_time else e.end_time})"
                    for e in events
                ]
                text_content = (
                    f"Found {len(events)} event(s) matching '{query}':\n" + "\n".join(lines)
                    if events
                    else f"No events found matching '{query}'."
                )
                return ToolOutput(
                    success=True,
                    result={"events": dict_events, "count": len(events)},
                    metadata={
                        "action": CalendarAction.SEARCH_EVENTS.value,
                        "count": len(events),
                        "formatted": text_content,
                    },
                )

            elif action_name in (CalendarAction.GET_EVENT.value, "get"):
                event_id = str(args.get("event_id") or args.get("id") or "").strip()
                if not event_id:
                    raise CalendarValidationError("Missing required 'event_id' argument.")
                ev = self._provider.get_event(event_id)
                formatted = (
                    f"Event: {ev.title} [{ev.id}]\n"
                    f"When: {ev.start_time} - {ev.end_time}\n"
                    f"Location: {ev.location or 'Not specified'}\n"
                    f"Attendees: {', '.join(ev.attendees) if ev.attendees else 'None'}\n"
                    f"Description: {ev.description or 'No description'}"
                )
                return ToolOutput(
                    success=True,
                    result=ev.to_dict(),
                    metadata={
                        "action": CalendarAction.GET_EVENT.value,
                        "event_id": ev.id,
                        "title": ev.title,
                        "formatted": formatted,
                    },
                )

            elif action_name in (CalendarAction.CHECK_CONFLICTS.value, "conflicts"):
                start_time = str(args.get("start_time") or "").strip()
                end_time = str(args.get("end_time") or "").strip()
                exclude_id = args.get("exclude_event_id") or args.get("event_id")
                if not start_time or not end_time:
                    raise CalendarValidationError("Missing 'start_time' or 'end_time' to check conflicts.")

                conflicts = self._provider.check_conflicts(
                    start_time=start_time,
                    end_time=end_time,
                    exclude_event_id=str(exclude_id) if exclude_id else None,
                )
                lines = [f"- {e.title} ({e.start_time} - {e.end_time})" for e in conflicts]
                text_content = (
                    f"Detected {len(conflicts)} scheduling conflict(s):\n" + "\n".join(lines)
                    if conflicts
                    else "No scheduling conflicts detected for this timeframe."
                )
                return ToolOutput(
                    success=True,
                    result={"conflicts": [e.to_dict() for e in conflicts], "has_conflicts": len(conflicts) > 0},
                    metadata={
                        "action": CalendarAction.CHECK_CONFLICTS.value,
                        "has_conflicts": len(conflicts) > 0,
                        "formatted": text_content,
                    },
                )

            elif action_name in (CalendarAction.CREATE_EVENT.value, "create"):
                title = str(args.get("title") or args.get("name") or "").strip()
                start_time = str(args.get("start_time") or "").strip()
                end_time = str(args.get("end_time") or "").strip()
                description = str(args.get("description") or "")
                location = str(args.get("location") or "")

                raw_att = args.get("attendees")
                if isinstance(raw_att, str):
                    attendees = tuple(a.strip() for a in raw_att.split(",") if a.strip())
                elif isinstance(raw_att, (list, tuple)):
                    attendees = tuple(str(a) for a in raw_att)
                else:
                    attendees = ()

                ev = self._provider.create_event(
                    title=title,
                    start_time=start_time,
                    end_time=end_time,
                    description=description,
                    location=location,
                    attendees=attendees,
                )
                formatted = f"Created calendar event '{ev.title}' on {ev.start_time} (ID: {ev.id})."
                return ToolOutput(
                    success=True,
                    result=ev.to_dict(),
                    metadata={
                        "action": CalendarAction.CREATE_EVENT.value,
                        "event_id": ev.id,
                        "title": ev.title,
                        "verified": True,
                        "status": "created",
                        "formatted": formatted,
                    },
                )

            elif action_name in (CalendarAction.MODIFY_EVENT.value, "modify", "update", "reschedule"):
                event_id = str(args.get("event_id") or args.get("id") or "").strip()
                if not event_id:
                    raise CalendarValidationError("Missing required 'event_id' argument to modify event.")

                title = args.get("title")
                start_time = args.get("start_time")
                end_time = args.get("end_time")
                description = args.get("description")
                location = args.get("location")

                raw_att = args.get("attendees")
                attendees = None
                if isinstance(raw_att, str):
                    attendees = tuple(a.strip() for a in raw_att.split(",") if a.strip())
                elif isinstance(raw_att, (list, tuple)):
                    attendees = tuple(str(a) for a in raw_att)

                ev = self._provider.modify_event(
                    event_id=event_id,
                    title=str(title) if title is not None else None,
                    start_time=str(start_time) if start_time is not None else None,
                    end_time=str(end_time) if end_time is not None else None,
                    description=str(description) if description is not None else None,
                    location=str(location) if location is not None else None,
                    attendees=attendees,
                )
                formatted = f"Updated calendar event '{ev.title}' (ID: {ev.id}) to {ev.start_time} - {ev.end_time}."
                return ToolOutput(
                    success=True,
                    result=ev.to_dict(),
                    metadata={
                        "action": CalendarAction.MODIFY_EVENT.value,
                        "event_id": ev.id,
                        "title": ev.title,
                        "verified": True,
                        "status": "modified",
                        "formatted": formatted,
                    },
                )

            elif action_name in (CalendarAction.CANCEL_EVENT.value, "cancel", "delete"):
                event_id = str(args.get("event_id") or args.get("id") or "").strip()
                if not event_id:
                    raise CalendarValidationError("Missing required 'event_id' argument to cancel event.")

                receipt = self._provider.cancel_event(event_id)
                formatted = f"Cancelled calendar event '{receipt.title}' (ID: {receipt.event_id})."
                return ToolOutput(
                    success=True,
                    result=receipt.to_dict(),
                    metadata={
                        "action": CalendarAction.CANCEL_EVENT.value,
                        "event_id": receipt.event_id,
                        "title": receipt.title,
                        "verified": True,
                        "status": "cancelled",
                        "formatted": formatted,
                    },
                )

            else:
                raise CalendarValidationError(f"Unknown calendar action: '{action_name}'")

        except CalendarError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={
                    "action": action_name,
                    "error": type(exc).__name__,
                    "formatted": f"Calendar operation failed: {exc}",
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={
                    "action": action_name,
                    "error": "unexpected_error",
                    "formatted": f"Calendar operation failed: {exc}",
                },
            )

    execute = run


class CalendarTool(BaseTool):
    """Tool exposing calendar operations to Mamba."""

    def __init__(self, provider: CalendarProvider | None = None) -> None:
        tool = Tool(
            name="calendar",
            description="Manage, search, create, modify, and cancel calendar events.",
            metadata={"tool": "calendar"},
        )
        handler = CalendarHandler(provider=provider)
        super().__init__(tool=tool, handler=handler)

