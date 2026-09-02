"""Filesystem tool errors."""

from __future__ import annotations

from tools.errors import ToolError, ToolExecutionError, ToolValidationError


class FilesystemError(ToolError):
    """Base error for all filesystem tool operations."""


class FilesystemPathError(FilesystemError, ToolValidationError):
    """Raised when a filesystem path is invalid or violates constraints."""


class FilesystemOperationError(FilesystemError, ToolExecutionError):
    """Raised when a filesystem operation fails."""

