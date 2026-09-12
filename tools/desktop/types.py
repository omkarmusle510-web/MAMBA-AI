"""Types and definitions for desktop tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class DesktopAction(StrEnum):
    """Supported desktop operations."""

    GET_FOREGROUND_WINDOW = "get_foreground_window"
    GET_WINDOW_TITLE = "get_window_title"
    FIND_WINDOW = "find_window"
    FOCUS_WINDOW = "focus_window"
    CLOSE_WINDOW = "close_window"
    READ_CLIPBOARD = "read_clipboard"
    WRITE_CLIPBOARD = "write_clipboard"
    CLEAR_CLIPBOARD = "clear_clipboard"


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """Information about an OS window."""

    hwnd: int
    title: str
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "visible": self.visible,
        }


@dataclass(frozen=True, slots=True)
class DesktopOperationDefinition:
    """Metadata definition for a desktop operation."""

    name: str
    description: str
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
        }


DESKTOP_OPERATIONS: dict[DesktopAction, DesktopOperationDefinition] = {
    DesktopAction.GET_FOREGROUND_WINDOW: DesktopOperationDefinition(
        name=DesktopAction.GET_FOREGROUND_WINDOW.value,
        description="Get HWND and title of the currently focused/active foreground window.",
        risk_level=RiskLevel.LOW,
        destructive=False,
    ),
    DesktopAction.GET_WINDOW_TITLE: DesktopOperationDefinition(
        name=DesktopAction.GET_WINDOW_TITLE.value,
        description="Get the title of a specific window by HWND or current foreground window.",
        risk_level=RiskLevel.LOW,
        destructive=False,
    ),
    DesktopAction.FIND_WINDOW: DesktopOperationDefinition(
        name=DesktopAction.FIND_WINDOW.value,
        description="Search visible windows matching a title substring.",
        risk_level=RiskLevel.LOW,
        destructive=False,
    ),
    DesktopAction.FOCUS_WINDOW: DesktopOperationDefinition(
        name=DesktopAction.FOCUS_WINDOW.value,
        description="Bring a target window to the foreground and focus it.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
    ),
    DesktopAction.CLOSE_WINDOW: DesktopOperationDefinition(
        name=DesktopAction.CLOSE_WINDOW.value,
        description="Send WM_CLOSE to request closing a target window.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
    ),
    DesktopAction.READ_CLIPBOARD: DesktopOperationDefinition(
        name=DesktopAction.READ_CLIPBOARD.value,
        description="Read text content from the system clipboard.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=True,
    ),
    DesktopAction.WRITE_CLIPBOARD: DesktopOperationDefinition(
        name=DesktopAction.WRITE_CLIPBOARD.value,
        description="Write text content to the system clipboard.",
        risk_level=RiskLevel.MEDIUM,
        destructive=False,
    ),
    DesktopAction.CLEAR_CLIPBOARD: DesktopOperationDefinition(
        name=DesktopAction.CLEAR_CLIPBOARD.value,
        description="Clear text content from the system clipboard.",
        risk_level=RiskLevel.MEDIUM,
        destructive=False,
    ),
}

