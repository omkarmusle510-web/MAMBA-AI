"""Error types for calendar capabilities."""

from __future__ import annotations

from tools.errors import ToolError


class CalendarError(ToolError):
    """Base error for calendar operations."""


class CalendarProviderError(CalendarError):
    """Raised when an underlying calendar provider fails."""


class CalendarEventNotFoundError(CalendarError):
    """Raised when a requested calendar event is not found."""


class CalendarConflictError(CalendarError):
    """Raised when an event scheduling conflict is detected."""


class CalendarValidationError(CalendarError):
    """Raised when calendar arguments or parameters are invalid."""

