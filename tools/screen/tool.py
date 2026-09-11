"""Screen tool factories for Mamba."""

from __future__ import annotations

from tools.tool import BaseTool

from .ocr import OCRTool, RegionOCRTool
from .screenshot import RegionScreenshotTool, ScreenshotTool
from .types import ScreenAction


def create_screen_tools() -> dict[str, BaseTool]:
    """Create all standard screen tools."""
    return {
        ScreenAction.SCREENSHOT.value: ScreenshotTool(),
        ScreenAction.REGION_SCREENSHOT.value: RegionScreenshotTool(),
        ScreenAction.OCR.value: OCRTool(),
        ScreenAction.REGION_OCR.value: RegionOCRTool(),
    }

