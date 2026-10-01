"""Desktop tool factories and registry for Mamba."""

from __future__ import annotations

from tools.tool import BaseTool

from .clipboard import (
    ClearClipboardHandler,
    ClearClipboardTool,
    ReadClipboardHandler,
    ReadClipboardTool,
    WriteClipboardHandler,
    WriteClipboardTool,
)
from .apps import (
    APP_CALCULATOR,
    APP_FILE_EXPLORER,
    APP_NOTEPAD,
    APP_VSCODE,
    ApplicationAdapter,
    ApplicationRegistry,
    default_application_registry,
)
from .cross_app_tools import (
    InspectApplicationsHandler,
    InspectApplicationsTool,
    LaunchApplicationHandler,
    LaunchApplicationTool,
    ReadApplicationTextHandler,
    ReadApplicationTextTool,
    TypeTextInApplicationHandler,
    TypeTextInApplicationTool,
)
from .driver import ApplicationUnavailableError, BoundApplicationDriver, CrossAppDriver
from .notepad import (
    NotepadDriver,
    NotepadUnavailableError,
    PyAutoGuiTextEntry,
    TargetResolutionError,
    TextEntry,
    WindowBinding,
    WindowsNotepadDriver,
)
from .notepad_tools import (
    LaunchNotepadHandler,
    LaunchNotepadTool,
    ReadNotepadTextHandler,
    ReadNotepadTextTool,
    TypeTextInNotepadHandler,
    TypeTextInNotepadTool,
)
from .observation import (
    ClipboardCopyProbe,
    ObservationProbe,
    ObservationResult,
    TextControlProbe,
    WindowStateProbe,
)
from .types import DesktopAction
from .window import (
    CloseWindowHandler,
    CloseWindowTool,
    FindWindowHandler,
    FindWindowTool,
    FocusWindowHandler,
    FocusWindowTool,
    GetForegroundWindowHandler,
    GetForegroundWindowTool,
    GetWindowTitleHandler,
    GetWindowTitleTool,
)


def create_desktop_tools() -> dict[str, BaseTool]:
    """Create all standard desktop tools."""
    return {
        DesktopAction.GET_FOREGROUND_WINDOW.value: GetForegroundWindowTool(),
        DesktopAction.GET_WINDOW_TITLE.value: GetWindowTitleTool(),
        DesktopAction.FIND_WINDOW.value: FindWindowTool(),
        DesktopAction.FOCUS_WINDOW.value: FocusWindowTool(),
        DesktopAction.CLOSE_WINDOW.value: CloseWindowTool(),
        DesktopAction.READ_CLIPBOARD.value: ReadClipboardTool(),
        DesktopAction.WRITE_CLIPBOARD.value: WriteClipboardTool(),
        DesktopAction.CLEAR_CLIPBOARD.value: ClearClipboardTool(),
        DesktopAction.LAUNCH_APPLICATION.value: LaunchApplicationTool(),
        DesktopAction.TYPE_TEXT_IN_APPLICATION.value: TypeTextInApplicationTool(),
        DesktopAction.READ_APPLICATION_TEXT.value: ReadApplicationTextTool(),
        DesktopAction.INSPECT_APPLICATIONS.value: InspectApplicationsTool(),
        DesktopAction.LAUNCH_NOTEPAD.value: LaunchNotepadTool(),
        DesktopAction.TYPE_TEXT_IN_NOTEPAD.value: TypeTextInNotepadTool(),
        DesktopAction.READ_NOTEPAD_TEXT.value: ReadNotepadTextTool(),
    }


__all__ = [
    "APP_CALCULATOR",
    "APP_FILE_EXPLORER",
    "APP_NOTEPAD",
    "APP_VSCODE",
    "ApplicationAdapter",
    "ApplicationRegistry",
    "ApplicationUnavailableError",
    "BoundApplicationDriver",
    "ClipboardCopyProbe",
    "CrossAppDriver",
    "InspectApplicationsHandler",
    "InspectApplicationsTool",
    "LaunchApplicationHandler",
    "LaunchApplicationTool",
    "LaunchNotepadHandler",
    "LaunchNotepadTool",
    "NotepadDriver",
    "NotepadUnavailableError",
    "ObservationProbe",
    "ObservationResult",
    "PyAutoGuiTextEntry",
    "ReadApplicationTextHandler",
    "ReadApplicationTextTool",
    "ReadNotepadTextHandler",
    "ReadNotepadTextTool",
    "TargetResolutionError",
    "TextControlProbe",
    "TextEntry",
    "TypeTextInApplicationHandler",
    "TypeTextInApplicationTool",
    "TypeTextInNotepadHandler",
    "TypeTextInNotepadTool",
    "WindowBinding",
    "WindowStateProbe",
    "WindowsNotepadDriver",
    "create_desktop_tools",
    "default_application_registry",
]

