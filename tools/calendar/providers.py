"""Calendar provider protocol and development simulation implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, Sequence
from uuid import uuid4

from .errors import CalendarEventNotFoundError, CalendarValidationError
from .types import CalendarEvent, CalendarMutationReceipt


class CalendarProvider(Protocol):
    """Protocol for calendar service providers."""

    def list_events(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        max_results: int = 10,
    ) -> Sequence[CalendarEvent]:
        """List upcoming events within an optional timeframe."""
        ...

    def search_events(self, query: str, *, max_results: int = 5) -> Sequence[CalendarEvent]:
        """Search events matching query."""
        ...

    def get_event(self, event_id: str) -> CalendarEvent:
        """Get full details of a specific event."""
        ...

    def create_event(
        self,
        *,
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        attendees: tuple[str, ...] = (),
    ) -> CalendarEvent:
        """Create a new event on the calendar."""
        ...

    def modify_event(
        self,
        *,
        event_id: str,
        title: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: tuple[str, ...] | None = None,
    ) -> CalendarEvent:
        """Modify fields or reschedule an existing event."""
        ...

    def cancel_event(self, event_id: str) -> CalendarMutationReceipt:
        """Cancel an event from the calendar."""
        ...

    def check_conflicts(
        self,
        *,
        start_time: str,
        end_time: str,
        exclude_event_id: str | None = None,
    ) -> Sequence[CalendarEvent]:
        """Return any existing events that overlap with the specified window."""
        ...


class SimulatedCalendarProvider:
    """Development and testing calendar simulation provider.

    Explicitly tagged as a development/simulation provider, not a production fallback.
    Provides deterministic calendar state for tests and local interactive development.
    """

    def __init__(self, initial_events: Sequence[CalendarEvent] | None = None) -> None:
        self.provider_name = "simulated_calendar"
        self._events: dict[str, CalendarEvent] = {}

        if initial_events is not None:
            for ev in initial_events:
                self._events[ev.id] = ev
        else:
            self._populate_sample_calendar()

    def _populate_sample_calendar(self) -> None:
        sample_events = [
            CalendarEvent(
                id="cal-201",
                title="Project Mamba Sync with Rahul",
                start_time="2026-09-14T16:00:00",
                end_time="2026-09-14T17:00:00",
                description="Discussion on real-world communication skills and productivity workflows.",
                location="Google Meet",
                attendees=("user@mamba.ai", "rahul@partner.org"),
            ),
            CalendarEvent(
                id="cal-202",
                title="Engineering Team Standup",
                start_time="2026-09-14T09:30:00",
                end_time="2026-09-14T10:00:00",
                description="Daily engineering sync on blockers and release items.",
                location="Room 3B / Virtual",
                attendees=("team@mamba.ai",),
            ),
            CalendarEvent(
                id="cal-203",
                title="Architecture Review",
                start_time="2026-09-16T14:00:00",
                end_time="2026-09-16T15:00:00",
                description="Review provider boundaries and verification predicates.",
                location="Conference Room A",
                attendees=("user@mamba.ai", "architect@mamba.ai"),
            ),
        ]
        for ev in sample_events:
            self._events[ev.id] = ev

    def list_events(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        max_results: int = 10,
    ) -> Sequence[CalendarEvent]:
        active = [e for e in self._events.values() if e.status != "cancelled"]
        if start_time:
            active = [e for e in active if e.end_time >= start_time]
        if end_time:
            active = [e for e in active if e.start_time <= end_time]
        return tuple(sorted(active, key=lambda e: e.start_time)[:max_results])

    def search_events(self, query: str, *, max_results: int = 5) -> Sequence[CalendarEvent]:
        q = query.lower().strip()
        active = [e for e in self._events.values() if e.status != "cancelled"]
        matches = [
            e for e in active
            if q in e.title.lower()
            or q in e.description.lower()
            or q in e.location.lower()
            or any(q in a.lower() for a in e.attendees)
            or q in e.start_time.lower()
        ]
        return tuple(sorted(matches, key=lambda e: e.start_time)[:max_results])

    def get_event(self, event_id: str) -> CalendarEvent:
        event = self._events.get(event_id)
        if event is None or event.status == "cancelled":
            raise CalendarEventNotFoundError(f"Calendar event '{event_id}' was not found.")
        return event

    def check_conflicts(
        self,
        *,
        start_time: str,
        end_time: str,
        exclude_event_id: str | None = None,
    ) -> Sequence[CalendarEvent]:
        conflicts = []
        for e in self._events.values():
            if e.status == "cancelled":
                continue
            if exclude_event_id and e.id == exclude_event_id:
                continue
            # Two intervals [s1, e1) and [s2, e2) overlap if s1 < e2 and s2 < e1
            if start_time < e.end_time and e.start_time < end_time:
                conflicts.append(e)
        return tuple(conflicts)

    def create_event(
        self,
        *,
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        attendees: tuple[str, ...] = (),
    ) -> CalendarEvent:
        if not title.strip():
            raise CalendarValidationError("Cannot create calendar event without a title.")
        if not start_time or not end_time:
            raise CalendarValidationError("Cannot create calendar event without start_time and end_time.")

        event_id = f"cal-{uuid4().hex[:8]}"
        event = CalendarEvent(
            id=event_id,
            title=title.strip(),
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            attendees=attendees,
            status="confirmed",
        )
        self._events[event_id] = event
        return event

    def modify_event(
        self,
        *,
        event_id: str,
        title: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: tuple[str, ...] | None = None,
    ) -> CalendarEvent:
        existing = self.get_event(event_id)
        updated = CalendarEvent(
            id=existing.id,
            title=title if title is not None else existing.title,
            start_time=start_time if start_time is not None else existing.start_time,
            end_time=end_time if end_time is not None else existing.end_time,
            description=description if description is not None else existing.description,
            location=location if location is not None else existing.location,
            attendees=attendees if attendees is not None else existing.attendees,
            status=existing.status,
        )
        self._events[event_id] = updated
        return updated

    def cancel_event(self, event_id: str) -> CalendarMutationReceipt:
        existing = self.get_event(event_id)
        cancelled = CalendarEvent(
            id=existing.id,
            title=existing.title,
            start_time=existing.start_time,
            end_time=existing.end_time,
            description=existing.description,
            location=existing.location,
            attendees=existing.attendees,
            status="cancelled",
        )
        self._events[event_id] = cancelled
        now_str = datetime.now(UTC).isoformat()
        return CalendarMutationReceipt(
            action="cancelled",
            event_id=event_id,
            title=existing.title,
            timestamp=now_str,
            status="cancelled",
            verified=True,
        )

