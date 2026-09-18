"""Types and data structures for calendar capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class CalendarAction(StrEnum):
    """Supported calendar operations."""

    LIST_EVENTS = "list_events"
    SEARCH_EVENTS = "search_events"
    GET_EVENT = "get_event"
    CREATE_EVENT = "create_event"
    MODIFY_EVENT = "modify_event"
    CANCEL_EVENT = "cancel_event"
    CHECK_CONFLICTS = "check_conflicts"


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """Structured calendar event."""

    id: str
    title: str
    start_time: str
    end_time: str
    description: str = ""
    location: str = ""
    attendees: tuple[str, ...] = field(default_factory=tuple)
    status: str = "confirmed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "description": self.description,
            "location": self.location,
            "attendees": list(self.attendees),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class CalendarMutationReceipt:
    """Receipt returned after creating, modifying, or cancelling an event."""

    action: str
    event_id: str
    title: str
    timestamp: str
    status: str = "success"
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "event_id": self.event_id,
            "title": self.title,
            "timestamp": self.timestamp,
            "status": self.status,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class CalendarOperationDefinition:
    """Metadata definition for a calendar operation."""

    name: str
    description: str
    action: CalendarAction
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False
    externally_visible: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
            "externally_visible": self.externally_visible,
        }


CALENDAR_OPERATIONS: dict[CalendarAction, CalendarOperationDefinition] = {
    CalendarAction.LIST_EVENTS: CalendarOperationDefinition(
        name=CalendarAction.LIST_EVENTS.value,
        description="List upcoming events within a specified timeframe.",
        action=CalendarAction.LIST_EVENTS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    CalendarAction.SEARCH_EVENTS: CalendarOperationDefinition(
        name=CalendarAction.SEARCH_EVENTS.value,
        description="Search for calendar events matching a title, description, or attendee.",
        action=CalendarAction.SEARCH_EVENTS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    CalendarAction.GET_EVENT: CalendarOperationDefinition(
        name=CalendarAction.GET_EVENT.value,
        description="Get full details for a specific calendar event.",
        action=CalendarAction.GET_EVENT,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    CalendarAction.CHECK_CONFLICTS: CalendarOperationDefinition(
        name=CalendarAction.CHECK_CONFLICTS.value,
        description="Check if a proposed meeting slot conflicts with existing events.",
        action=CalendarAction.CHECK_CONFLICTS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    CalendarAction.CREATE_EVENT: CalendarOperationDefinition(
        name=CalendarAction.CREATE_EVENT.value,
        description="Create a new event on the calendar.",
        action=CalendarAction.CREATE_EVENT,
        risk_level=RiskLevel.HIGH,
        destructive=False,
        user_sensitive=True,
        irreversible=False,
        externally_visible=True,
    ),
    CalendarAction.MODIFY_EVENT: CalendarOperationDefinition(
        name=CalendarAction.MODIFY_EVENT.value,
        description="Modify or reschedule an existing calendar event.",
        action=CalendarAction.MODIFY_EVENT,
        risk_level=RiskLevel.HIGH,
        destructive=False,
        user_sensitive=True,
        irreversible=False,
        externally_visible=True,
    ),
    CalendarAction.CANCEL_EVENT: CalendarOperationDefinition(
        name=CalendarAction.CANCEL_EVENT.value,
        description="Cancel or delete a calendar event.",
        action=CalendarAction.CANCEL_EVENT,
        risk_level=RiskLevel.HIGH,
        destructive=True,
        user_sensitive=True,
        irreversible=True,
        externally_visible=True,
    ),
}

