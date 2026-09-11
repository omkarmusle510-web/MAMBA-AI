"""System tool factories for Mamba."""

from __future__ import annotations

from tools.tool import BaseTool

from .gpu import GpuInfoHandler, GpuInfoTool
from .system import SystemInfoHandler, SystemInfoTool
from .types import SystemAction


def create_system_tools() -> dict[str, BaseTool]:
    """Create all standard system tools."""
    return {
        SystemAction.SYSTEM_INFO.value: SystemInfoTool(),
        SystemAction.GPU_INFO.value: GpuInfoTool(),
    }

