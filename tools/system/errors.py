"""Errors for system tools."""

from __future__ import annotations

from tools.errors import ToolError


class SystemToolError(ToolError):
    """Base exception for system tool errors."""


class SystemInfoError(SystemToolError):
    """Error during system info collection."""


class GpuInfoError(SystemToolError):
    """Error during GPU info collection."""

