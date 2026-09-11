"""Types and definitions for screen tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class ScreenAction(StrEnum):
    """Supported screen operations."""

    SCREENSHOT = "screenshot"
    REGION_SCREENSHOT = "region_screenshot"
    OCR = "ocr"
    REGION_OCR = "region_ocr"


@dataclass(frozen=True, slots=True)
class ScreenOperationDefinition:
    """Metadata definition for a screen operation."""

    name: str
    description: str
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    user_sensitive: bool = True
    irreversible: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
        }


SCREEN_OPERATIONS: dict[ScreenAction, ScreenOperationDefinition] = {
    ScreenAction.SCREENSHOT: ScreenOperationDefinition(
        name=ScreenAction.SCREENSHOT.value,
        description="Capture a full-screen screenshot of the primary display.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=True,
    ),
    ScreenAction.REGION_SCREENSHOT: ScreenOperationDefinition(
        name=ScreenAction.REGION_SCREENSHOT.value,
        description="Capture a screenshot of a specified rectangular screen region.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=True,
    ),
    ScreenAction.OCR: ScreenOperationDefinition(
        name=ScreenAction.OCR.value,
        description="Capture the full screen and extract text via OCR.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=True,
    ),
    ScreenAction.REGION_OCR: ScreenOperationDefinition(
        name=ScreenAction.REGION_OCR.value,
        description="Capture a screen region and extract text via OCR.",
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=True,
    ),
}

