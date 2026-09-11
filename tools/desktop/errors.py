"""Errors for desktop tools."""

from __future__ import annotations

from tools.errors import ToolError


class DesktopToolError(ToolError):
    """Base exception for all desktop tool errors."""


class WindowError(DesktopToolError):
    """Error during window operation."""


class WindowNotFoundError(WindowError):
    """Requested window was not found."""


class ClipboardError(DesktopToolError):
    """Error during clipboard operation."""

