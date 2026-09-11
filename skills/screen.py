"""Screen skills and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.protocols import ToolExecutor
from tools.screen.ocr import OCRTool, RegionOCRTool
from tools.screen.screenshot import RegionScreenshotTool, ScreenshotTool
from tools.screen.types import SCREEN_OPERATIONS, ScreenAction
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_SCREENSHOT_INTENTS = frozenset(
    {"screenshot", "take_screenshot", "capture_screen", "screen_capture"}
)
_REGION_SCREENSHOT_INTENTS = frozenset(
    {"region_screenshot", "capture_region", "region_capture"}
)
_OCR_INTENTS = frozenset(
    {"ocr", "read_screen", "screen_text", "read_text"}
)
_REGION_OCR_INTENTS = frozenset(
    {"region_ocr", "read_region", "region_text", "region_read"}
)


class ScreenshotSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.SCREENSHOT]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or ScreenshotTool()
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


class RegionScreenshotSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.REGION_SCREENSHOT]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or RegionScreenshotTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        tool_input = ToolInput(
            arguments={
                "x": meta.get("x"),
                "y": meta.get("y"),
                "width": meta.get("width"),
                "height": meta.get("height"),
            },
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class OCRSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.OCR]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or OCRTool()
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


class RegionOCRSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.REGION_OCR]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or RegionOCRTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        tool_input = ToolInput(
            arguments={
                "x": meta.get("x"),
                "y": meta.get("y"),
                "width": meta.get("width"),
                "height": meta.get("height"),
            },
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


@dataclass(slots=True)
class ScreenTaskHandler:
    """Dispatches screen/vision task inputs to concrete skills."""

    screenshot_skill: ScreenshotSkill | None = None
    region_screenshot_skill: RegionScreenshotSkill | None = None
    ocr_skill: OCRSkill | None = None
    region_ocr_skill: RegionOCRSkill | None = None

    def __post_init__(self) -> None:
        if self.screenshot_skill is None:
            self.screenshot_skill = ScreenshotSkill()
        if self.region_screenshot_skill is None:
            self.region_screenshot_skill = RegionScreenshotSkill()
        if self.ocr_skill is None:
            self.ocr_skill = OCRSkill()
        if self.region_ocr_skill is None:
            self.region_ocr_skill = RegionOCRSkill()

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for this intent."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        if intent in _REGION_OCR_INTENTS:
            return SCREEN_OPERATIONS[ScreenAction.REGION_OCR].to_metadata()
        if intent in _OCR_INTENTS:
            return SCREEN_OPERATIONS[ScreenAction.OCR].to_metadata()
        if intent in _REGION_SCREENSHOT_INTENTS:
            return SCREEN_OPERATIONS[ScreenAction.REGION_SCREENSHOT].to_metadata()
        if intent in _SCREENSHOT_INTENTS:
            return SCREEN_OPERATIONS[ScreenAction.SCREENSHOT].to_metadata()

        return {"action": intent, "risk_level": "low", "user_sensitive": True}

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        skill_input = SkillInput.from_task(task_input, context)

        if intent in _SCREENSHOT_INTENTS:
            assert self.screenshot_skill is not None
            return self.screenshot_skill.run(skill_input).to_task_output()

        if intent in _REGION_SCREENSHOT_INTENTS:
            assert self.region_screenshot_skill is not None
            return self.region_screenshot_skill.run(skill_input).to_task_output()

        if intent in _OCR_INTENTS:
            assert self.ocr_skill is not None
            return self.ocr_skill.run(skill_input).to_task_output()

        if intent in _REGION_OCR_INTENTS:
            assert self.region_ocr_skill is not None
            return self.region_ocr_skill.run(skill_input).to_task_output()

        return TaskOutput(
            content=f"unsupported screen capability intent: '{task_input.intent}'",
            success=False,
            metadata={"error": "unsupported_capability"},
        )

