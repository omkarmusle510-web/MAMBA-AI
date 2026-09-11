"""Types and definitions for system tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class SystemAction(StrEnum):
    """Supported system operations."""

    SYSTEM_INFO = "system_info"
    GPU_INFO = "gpu_info"


@dataclass(frozen=True, slots=True)
class SystemOperationDefinition:
    """Metadata definition for a system operation."""

    name: str
    description: str
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
        }


SYSTEM_OPERATIONS: dict[SystemAction, SystemOperationDefinition] = {
    SystemAction.SYSTEM_INFO: SystemOperationDefinition(
        name=SystemAction.SYSTEM_INFO.value,
        description="Retrieve operating system, CPU, memory, disk, and uptime metrics.",
        risk_level=RiskLevel.LOW,
        destructive=False,
    ),
    SystemAction.GPU_INFO: SystemOperationDefinition(
        name=SystemAction.GPU_INFO.value,
        description="Retrieve NVIDIA GPU metrics via pynvml, or report unavailable cleanly.",
        risk_level=RiskLevel.LOW,
        destructive=False,
    ),
}

