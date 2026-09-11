"""Errors for screen tools."""

from __future__ import annotations

from tools.errors import ToolError


class ScreenToolError(ToolError):
    """Base exception for screen tool errors."""


class ScreenCaptureError(ScreenToolError):
    """Error during screen capture."""


class OCRError(ScreenToolError):
    """Error during OCR text extraction."""


class RegionValidationError(ScreenToolError):
    """Error when region coordinates are invalid."""

