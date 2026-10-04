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
    # ── Cross-application interaction: generic supported-application actions ──
    LAUNCH_APPLICATION = "launch_application"
    TYPE_TEXT_IN_APPLICATION = "type_text_in_application"
    READ_APPLICATION_TEXT = "read_application_text"
    INSPECT_APPLICATIONS = "inspect_applications"
    # ── Notepad-specific action names (thin aliases of the generic ones) ──
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
        description=(
            "Request closing the exact window recorded when it was bound (handle "
            "plus owning process identity, revalidated immediately before acting). "
            "Refuses stale, replaced, or ambiguously identified targets instead of "
            "guessing, because closing can discard unsaved work."
        ),
        # HIGH + destructive: closing a window can discard the user's unsaved
        # work. The existing permission policy maps HIGH -> ASK, so the action
        # now requires the user's confirmation through the single existing
        # mechanism; nothing is auto-authorized.
        risk_level=RiskLevel.HIGH,
        destructive=True,
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
    # ── Generic supported-application actions ──
    DesktopAction.LAUNCH_APPLICATION: DesktopOperationDefinition(
        name=DesktopAction.LAUNCH_APPLICATION.value,
        description=(
            "Launch a supported application (Notepad, Calculator, File Explorer, VS Code) "
            "and bind the window that appears as the target for later steps."
        ),
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
    ),
    DesktopAction.TYPE_TEXT_IN_APPLICATION: DesktopOperationDefinition(
        name=DesktopAction.TYPE_TEXT_IN_APPLICATION.value,
        description=(
            "Type text into a supported application window that has been explicitly "
            "bound and verified to be the active target. Refuses to type into anything "
            "that does not positively identify as the intended application."
        ),
        # Same classification as the Notepad-specific alias: an externally visible
        # mutation of another application's state, not destructive, not irreversible.
        risk_level=RiskLevel.MEDIUM,
        destructive=False,
        irreversible=False,
        user_sensitive=False,
        externally_visible=True,
    ),
    DesktopAction.READ_APPLICATION_TEXT: DesktopOperationDefinition(
        name=DesktopAction.READ_APPLICATION_TEXT.value,
        description=(
            "Read the currently observable content of a supported application window "
            "through its declared observation mechanism (used to observe and verify "
            "cross-application outcomes)."
        ),
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
    DesktopAction.INSPECT_APPLICATIONS: DesktopOperationDefinition(
        name=DesktopAction.INSPECT_APPLICATIONS.value,
        description=(
            "Report which applications Mamba supports and which of them currently have "
            "open, identifiable windows."
        ),
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
}


# ── Cross-application intent → action metadata ─────────────────────────────
# This is the single source of truth for cross-app intent classification, used
# by both the generic tools and the desktop skill handler.

_CROSS_APP_ACTIONS: dict[str, DesktopAction] = {
    # launching
    "launch_notepad": DesktopAction.LAUNCH_NOTEPAD,
    "open_notepad": DesktopAction.LAUNCH_NOTEPAD,
    "start_notepad": DesktopAction.LAUNCH_NOTEPAD,
    "launch_application": DesktopAction.LAUNCH_APPLICATION,
    "open_application": DesktopAction.LAUNCH_APPLICATION,
    "start_application": DesktopAction.LAUNCH_APPLICATION,
    "activate_application": DesktopAction.LAUNCH_APPLICATION,
    # typing
    "type_text": DesktopAction.TYPE_TEXT_IN_APPLICATION,
    "type_text_in_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "type_in_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "write_in_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "enter_text": DesktopAction.TYPE_TEXT_IN_APPLICATION,
    "type_into_notepad": DesktopAction.TYPE_TEXT_IN_NOTEPAD,
    "type_text_in_application": DesktopAction.TYPE_TEXT_IN_APPLICATION,
    "write_in_application": DesktopAction.TYPE_TEXT_IN_APPLICATION,
    # reading / observing
    "read_notepad_text": DesktopAction.READ_NOTEPAD_TEXT,
    "notepad_text": DesktopAction.READ_NOTEPAD_TEXT,
    "get_notepad_text": DesktopAction.READ_NOTEPAD_TEXT,
    "read_application_text": DesktopAction.READ_APPLICATION_TEXT,
    "application_text": DesktopAction.READ_APPLICATION_TEXT,
    "read_app_text": DesktopAction.READ_APPLICATION_TEXT,
    # discovery / inspection
    "inspect_applications": DesktopAction.INSPECT_APPLICATIONS,
    "list_applications": DesktopAction.INSPECT_APPLICATIONS,
    "supported_applications": DesktopAction.INSPECT_APPLICATIONS,
    "find_application": DesktopAction.INSPECT_APPLICATIONS,
}


def cross_app_action_for(intent: str) -> DesktopAction | None:
    """Return the cross-application desktop action bound to a planner intent."""
    return _CROSS_APP_ACTIONS.get(str(intent or "").strip().lower())


def cross_app_operation_metadata(intent: str) -> dict[str, Any] | None:
    """Return authoritative capability metadata for a cross-app intent, if any."""
    action = cross_app_action_for(intent)
    if action is None:
        return None
    return DESKTOP_OPERATIONS[action].to_metadata()


# Backwards-compatible Notepad-named helpers.
def notepad_operation_for(intent: str) -> DesktopAction | None:
    """Return the desktop action bound to a Notepad-flavoured intent, if any."""
    action = cross_app_action_for(intent)
    if action in (
        DesktopAction.LAUNCH_NOTEPAD,
        DesktopAction.TYPE_TEXT_IN_NOTEPAD,
        DesktopAction.READ_NOTEPAD_TEXT,
    ):
        return action
    return None


def notepad_operation_metadata(intent: str) -> dict[str, Any] | None:
    """Return authoritative capability metadata for a Notepad intent, if any."""
    action = notepad_operation_for(intent)
    if action is None:
        return None
    return DESKTOP_OPERATIONS[action].to_metadata()

