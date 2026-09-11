"""NVIDIA GPU metrics tool for Mamba."""

from __future__ import annotations

from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import GpuInfoError
from .types import SYSTEM_OPERATIONS, SystemAction


def _bytes_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}EB"


class GpuInfoHandler:
    """Handler for retrieving NVIDIA GPU metrics via pynvml."""

    def run(self, input: ToolInput) -> ToolOutput:
        try:
            import pynvml
        except ImportError:
            return ToolOutput(
                success=True,
                result="No NVIDIA GPU metrics available (pynvml is not installed).",
                metadata={"available": False, "gpu_count": 0, "gpus": []},
            )

        try:
            pynvml.nvmlInit()
        except Exception as exc:
            return ToolOutput(
                success=True,
                result=f"No NVIDIA GPU detected or NVML library unavailable ({exc}).",
                metadata={"available": False, "gpu_count": 0, "gpus": [], "reason": str(exc)},
            )

        try:
            device_count = pynvml.nvmlDeviceGetCount()
            if device_count == 0:
                return ToolOutput(
                    success=True,
                    result="No NVIDIA GPUs detected on this system.",
                    metadata={"available": False, "gpu_count": 0, "gpus": []},
                )

            gpus: list[dict[str, Any]] = []
            gpu_lines: list[str] = []

            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)

                # Memory
                try:
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    mem_total = _bytes_human(mem.total)
                    mem_used = _bytes_human(mem.used)
                    mem_free = _bytes_human(mem.free)
                    mem_percent = round((mem.used / mem.total) * 100, 1) if mem.total else 0.0
                except Exception:
                    mem_total = mem_used = mem_free = "unknown"
                    mem_percent = 0.0

                # Utilization
                try:
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu_util = util.gpu
                    mem_util = util.memory
                except Exception:
                    gpu_util = 0
                    mem_util = 0

                # Temperature
                try:
                    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except Exception:
                    temp = None

                gpu_data: dict[str, Any] = {
                    "index": i,
                    "name": name,
                    "memory": {
                        "total": mem_total,
                        "used": mem_used,
                        "free": mem_free,
                        "percent": mem_percent,
                    },
                    "utilization": {
                        "gpu_percent": gpu_util,
                        "memory_percent": mem_util,
                    },
                    "temperature_c": temp,
                }
                gpus.append(gpu_data)

                line = f"- GPU {i}: {name} | Load: {gpu_util}% | VRAM: {mem_used} / {mem_total} ({mem_percent}%)"
                if temp is not None:
                    line += f" | Temp: {temp}°C"
                gpu_lines.append(line)

            summary = f"Detected {device_count} NVIDIA GPU(s):\n" + "\n".join(gpu_lines)

            return ToolOutput(
                success=True,
                result=summary,
                metadata={
                    "available": True,
                    "gpu_count": device_count,
                    "gpus": gpus,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Error querying GPU metrics: {exc}",
                metadata={"available": False, "error": str(exc)},
            )
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass


class GpuInfoTool(BaseTool):
    def __init__(self) -> None:
        defn = SYSTEM_OPERATIONS[SystemAction.GPU_INFO]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=GpuInfoHandler(),
        )

