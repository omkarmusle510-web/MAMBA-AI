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
    OPEN_URL = "open_url"
    # ── Cross-application interaction (Phase 9): Notepad, explicitly bound ──
    LAUNCH_NOTEPAD = "launch_notepad"
    TYPE_TEXT_IN_NOTEPAD = "type_text_in_notepad"
    READ_NOTEPAD_TEXT = "read_notepad_text"


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
    DesktopAction.OPEN_URL: DesktopOperationDefinition(
        name=DesktopAction.OPEN_URL.value,
        description="Safely open a web URL in the default system browser.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
    ),
    DesktopAction.LAUNCH_NOTEPAD: DesktopOperationDefinition(
        name=DesktopAction.LAUNCH_NOTEPAD.value,
        description=(
            "Launch the Windows Notepad application and bind the newly created "
            "Notepad window as the target for later steps."
        ),
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
    ),
    DesktopAction.TYPE_TEXT_IN_NOTEPAD: DesktopOperationDefinition(
        name=DesktopAction.TYPE_TEXT_IN_NOTEPAD.value,
        description=(
            "Type text into an explicitly bound Notepad window that is verified to be "
            "the active foreground target. Refuses to type anywhere else."
        ),
        # MEDIUM: a real, externally visible mutation of another application's document
        # state, but not destructive and not irreversible (Notepad text is editable and
        # the user can undo it). The existing policy maps MEDIUM -> ALLOW, so this keeps
        # the confirmation mechanism single-sourced: only HIGH-risk actions ASK.
        risk_level=RiskLevel.MEDIUM,
        destructive=False,
        irreversible=False,
        user_sensitive=False,
        externally_visible=True,
    ),
    DesktopAction.READ_NOTEPAD_TEXT: DesktopOperationDefinition(
        name=DesktopAction.READ_NOTEPAD_TEXT.value,
        description=(
            "Read the current text content of an explicitly bound Notepad window "
            "(used to observe and verify Notepad interactions)."
        ),
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
}


# ── Notepad (cross-application) action metadata ────────────────────────────

_NOTEPAD_ACTIONS: dict[str, DesktopAction] = {
    "launch_notepad": DesktopAction.LAUNCH_NOTEPAD,
    "open_notepad": DesktopAction.LAUNCH_NOTEPAD,
    "start_notepad": DesktopAction.LAUNCH_NOTEPAD,
    "type_text": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "type_text_in_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "type_in_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "write_in_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "enter_text": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "type_into_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "read_notepad_text": DesktopAction.READ_NOTEPAD_TEXT,
    "notepad_text": DesktopAction.READ_NOTEPAD_TEXT,
    "get_notepad_text": DesktopAction.READ_NOTEPAD_TEXT,
}


def notepad_operation_for(intent: str) -> DesktopAction | None:
    """Return the Notepad desktop action bound to a planner intent, if any."""
    return _NOTEPAD_ACTIONS.get(str(intent or "").strip().lower())


def notepad_operation_metadata(intent: str) -> dict[str, Any] | None:
    """Return authoritative capability metadata for a Notepad intent, if any."""
    action = notepad_operation_for(intent)
    if action is None:
        return None
    return DESKTOP_OPERATIONS[action].to_metadata()

