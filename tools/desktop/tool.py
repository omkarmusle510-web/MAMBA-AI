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
        DesktopAction.LAUNCH_NOTEPAD.value: LaunchNotepadTool(),
        DesktopAction.TYPE_TEXT_IN_NOTEPAD.value: TypeTextInNotepadTool(),
        DesktopAction.READ_NOTEPAD_TEXT.value: ReadNotepadTextTool(),
    }


__all__ = [
    "LaunchNotepadHandler",
    "LaunchNotepadTool",
    "NotepadDriver",
    "NotepadUnavailableError",
    "PyAutoGuiTextEntry",
    "ReadNotepadTextHandler",
    "ReadNotepadTextTool",
    "TargetResolutionError",
    "TextEntry",
    "TypeTextInNotepadHandler",
    "TypeTextInNotepadTool",
    "WindowBinding",
    "WindowsNotepadDriver",
    "create_desktop_tools",
]

