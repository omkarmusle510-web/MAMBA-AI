"""System skills and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.protocols import ToolExecutor
from tools.system.gpu import GpuInfoTool
from tools.system.system import SystemInfoTool
from tools.system.types import SYSTEM_OPERATIONS, SystemAction
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_SYSTEM_INFO_INTENTS = frozenset(
    {"system_info", "sys_info", "system_status", "os_info", "host_info", "specs", "system"}
)
_GPU_INFO_INTENTS = frozenset(
    {"gpu_info", "nvidia_info", "cuda_info", "gpu_status", "gpu"}
)


class SystemInfoSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = SYSTEM_OPERATIONS[SystemAction.SYSTEM_INFO]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or SystemInfoTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        tool_input = ToolInput(
            arguments=dict(input.task_input.step_metadata),
            metadata=dict(input.task_input.step_metadata),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class GpuInfoSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = SYSTEM_OPERATIONS[SystemAction.GPU_INFO]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or GpuInfoTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        tool_input = ToolInput(
            arguments=dict(input.task_input.step_metadata),
            metadata=dict(input.task_input.step_metadata),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


@dataclass(slots=True)
class SystemTaskHandler:
    """Dispatches system and GPU task inputs to concrete skills."""

    system_info_skill: SystemInfoSkill | None = None
    gpu_info_skill: GpuInfoSkill | None = None

    def __post_init__(self) -> None:
        if self.system_info_skill is None:
            self.system_info_skill = SystemInfoSkill()
        if self.gpu_info_skill is None:
            self.gpu_info_skill = GpuInfoSkill()

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for this intent."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        if intent in _GPU_INFO_INTENTS:
            return SYSTEM_OPERATIONS[SystemAction.GPU_INFO].to_metadata()
        if intent in _SYSTEM_INFO_INTENTS:
            return SYSTEM_OPERATIONS[SystemAction.SYSTEM_INFO].to_metadata()

        return {"action": intent, "risk_level": "low"}

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        skill_input = SkillInput.from_task(task_input, context)

        if intent in _SYSTEM_INFO_INTENTS:
            assert self.system_info_skill is not None
            return self.system_info_skill.run(skill_input).to_task_output()

        if intent in _GPU_INFO_INTENTS:
            assert self.gpu_info_skill is not None
            return self.gpu_info_skill.run(skill_input).to_task_output()

        return TaskOutput(
            content=f"unsupported system capability intent: '{task_input.intent}'",
            success=False,
            metadata={"error": "unsupported_capability"},
        )

