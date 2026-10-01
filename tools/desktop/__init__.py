"""Mamba Desktop tools layer."""

from .clipboard import (
    ClearClipboardHandler,
    ClearClipboardTool,
    ReadClipboardHandler,
    ReadClipboardTool,
    WriteClipboardHandler,
    WriteClipboardTool,
)
from .errors import (
    ClipboardError,
    DesktopToolError,
    WindowError,
    WindowNotFoundError,
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
from .tool import create_desktop_tools
from .types import (
    DESKTOP_OPERATIONS,
    DesktopAction,
    DesktopOperationDefinition,
    WindowInfo,
    notepad_operation_for,
    notepad_operation_metadata,
)
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

__all__ = [
    "ClearClipboardHandler",
    "ClearClipboardTool",
    "ClipboardError",
    "CloseWindowHandler",
    "CloseWindowTool",
    "DESKTOP_OPERATIONS",
    "DesktopAction",
    "DesktopOperationDefinition",
    "DesktopToolError",
    "FindWindowHandler",
    "FindWindowTool",
    "FocusWindowHandler",
    "FocusWindowTool",
    "GetForegroundWindowHandler",
    "GetForegroundWindowTool",
    "GetWindowTitleHandler",
    "GetWindowTitleTool",
    "LaunchNotepadHandler",
    "LaunchNotepadTool",
    "NotepadDriver",
    "NotepadUnavailableError",
    "PyAutoGuiTextEntry",
    "ReadClipboardHandler",
    "ReadClipboardTool",
    "ReadNotepadTextHandler",
    "ReadNotepadTextTool",
    "TargetResolutionError",
    "TextEntry",
    "TypeTextInNotepadHandler",
    "TypeTextInNotepadTool",
    "WindowBinding",
    "WindowError",
    "WindowInfo",
    "WindowNotFoundError",
    "WindowsNotepadDriver",
    "WriteClipboardHandler",
    "WriteClipboardTool",
    "create_desktop_tools",
    "notepad_operation_for",
    "notepad_operation_metadata",
]

