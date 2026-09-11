"""System information tool for Mamba."""

from __future__ import annotations

import datetime
import platform
from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import SystemInfoError
from .types import SYSTEM_OPERATIONS, SystemAction


def _bytes_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}EB"


class SystemInfoHandler:
    """Handler for collecting core OS, CPU, memory, disk, and uptime metrics."""

    def run(self, input: ToolInput) -> ToolOutput:
        try:
            import psutil
        except ImportError:
            return ToolOutput(
                success=False,
                error="psutil is not installed.",
                metadata={"available": False},
            )

        try:
            # CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_count_logical = psutil.cpu_count(logical=True) or 1
            cpu_count_physical = psutil.cpu_count(logical=False) or cpu_count_logical

            # Memory
            vm = psutil.virtual_memory()
            ram_total_str = _bytes_human(vm.total)
            ram_used_str = _bytes_human(vm.used)
            ram_percent = vm.percent

            # Disks
            disks: dict[str, dict[str, Any]] = {}
            seen: set[str] = set()
            try:
                for part in psutil.disk_partitions(all=False):
                    mp = part.mountpoint
                    if mp in seen:
                        continue
                    seen.add(mp)
                    try:
                        du = psutil.disk_usage(mp)
                        disks[mp] = {
                            "total": _bytes_human(du.total),
                            "used": _bytes_human(du.used),
                            "free": _bytes_human(du.free),
                            "percent": du.percent,
                        }
                    except Exception:
                        continue
            except Exception:
                pass

            # Boot / Uptime
            boot_time = psutil.boot_time()
            uptime = datetime.datetime.now() - datetime.datetime.fromtimestamp(boot_time)
            uptime_str = str(uptime).split(".")[0]

            # Platform
            os_name = platform.system()
            os_release = platform.release()
            os_version = platform.version()
            machine = platform.machine()
            full_platform = platform.platform()

            summary = (
                f"OS: {os_name} {os_release} ({machine})\n"
                f"CPU: {cpu_percent}% ({cpu_count_physical} physical core(s), {cpu_count_logical} logical thread(s))\n"
                f"RAM: {ram_percent}% ({ram_used_str} / {ram_total_str})\n"
                f"Disks: {len(disks)} partition(s) monitored\n"
                f"Uptime: {uptime_str}"
            )

            metadata: dict[str, Any] = {
                "os": {
                    "system": os_name,
                    "release": os_release,
                    "version": os_version,
                    "machine": machine,
                    "platform": full_platform,
                },
                "cpu": {
                    "percent": cpu_percent,
                    "physical_cores": cpu_count_physical,
                    "logical_cores": cpu_count_logical,
                },
                "ram": {
                    "total": ram_total_str,
                    "used": ram_used_str,
                    "percent": ram_percent,
                    "raw_total": vm.total,
                    "raw_used": vm.used,
                },
                "disks": disks,
                "uptime": uptime_str,
                "uptime_seconds": int(uptime.total_seconds()),
            }

            return ToolOutput(
                success=True,
                result=summary,
                metadata=metadata,
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"System information collection failed: {exc}",
                metadata={"error": str(exc)},
            )


class SystemInfoTool(BaseTool):
    def __init__(self) -> None:
        defn = SYSTEM_OPERATIONS[SystemAction.SYSTEM_INFO]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=SystemInfoHandler(),
        )

