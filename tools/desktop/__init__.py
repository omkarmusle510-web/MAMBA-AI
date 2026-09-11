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
from .tool import create_desktop_tools
from .types import (
    DESKTOP_OPERATIONS,
    DesktopAction,
    DesktopOperationDefinition,
    WindowInfo,
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
    "ReadClipboardHandler",
    "ReadClipboardTool",
    "WindowError",
    "WindowInfo",
    "WindowNotFoundError",
    "WriteClipboardHandler",
    "WriteClipboardTool",
    "create_desktop_tools",
]

