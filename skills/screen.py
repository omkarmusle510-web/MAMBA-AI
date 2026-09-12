"""Screen skills and task handler for Mamba."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from models.errors import ModelRoutingError
from models.protocols import ModelRouter
from models.types import (
    ModelImagePart,
    ModelMessage,
    ModelRequest,
    ModelTextPart,
)
from tasks.types import TaskInput, TaskOutput
from tools.protocols import ToolExecutor
from tools.screen.ocr import OCRTool, RegionOCRTool
from tools.screen.screenshot import (
    RegionScreenshotTool,
    ScreenshotTool,
    clear_latest_capture,
    get_latest_capture,
)
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
_VISUAL_UNDERSTANDING_INTENTS = frozenset(
    {
        "visual_understanding",
        "understand_screen",
        "describe_screen",
        "look_at_screen",
        "screen_understanding",
        "interpret_screen",
    }
)

_VISUAL_SYSTEM_INSTRUCTION = (
    "You are Mamba's visual observation component. "
    "Interpret only what is visible in the provided screenshot. "
    "Answer the user's visual question clearly and directly. "
    "Do not invent UI elements that are not visible. "
    "Do not propose or claim to perform mouse, keyboard, or other actions. "
    "Your role is observation and interpretation only."
)


def _image_to_png_bytes(image: Any) -> bytes:
    """Encode a PIL image to PNG bytes without writing to disk."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _resolve_visual_question(input: SkillInput) -> str:
    """Build the user visual question from planner metadata / goal."""
    meta = input.task_input.step_metadata
    for key in ("question", "query", "prompt"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if input.task_input.description and input.task_input.description.strip():
        return input.task_input.description.strip()

    goal = input.task_input.goal or (
        input.context.request.goal if input.context and input.context.request else ""
    )
    if goal and goal.strip():
        return goal.strip()

    return "Describe what is visible on the screen."


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


class VisualUnderstandingSkill(BaseSkill):
    """Interpret the current screen via a multimodal model provider."""

    def __init__(
        self,
        *,
        model_router: ModelRouter,
        screenshot_tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
        system_instruction: str | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.VISUAL_UNDERSTANDING]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._router = model_router
        self._screenshot_tool = screenshot_tool or ScreenshotTool()
        self._executor = executor or StandardToolExecutor()
        self._system_instruction = system_instruction or _VISUAL_SYSTEM_INSTRUCTION
        self._model_parameters = model_parameters or {
            "temperature": 0,
            "max_tokens": 1024,
        }

    def execute(self, input: SkillInput) -> SkillOutput:
        action = str(input.task_input.step_metadata.get("action") or "").strip().lower()
        raw_intent = (input.task_input.intent or "").strip().lower()
        intent = action if action in _VISUAL_UNDERSTANDING_INTENTS else raw_intent

        if intent not in _VISUAL_UNDERSTANDING_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )

        auth_meta = SCREEN_OPERATIONS[ScreenAction.VISUAL_UNDERSTANDING].to_metadata()
        question = _resolve_visual_question(input)

        # Ensure an ephemeral in-memory capture exists via the existing path.
        image = get_latest_capture()
        if image is None:
            capture = self._executor.execute(
                self._screenshot_tool,
                ToolInput(arguments={}, metadata={}),
            )
            if not capture.success:
                return SkillOutput(
                    content=capture.error or "Screenshot capture failed",
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "screenshot_capture_failed",
                    },
                )
            image = get_latest_capture()

        if image is None:
            return SkillOutput(
                content="No screenshot available for visual understanding",
                success=False,
                metadata={
                    **auth_meta,
                    "error": "no_screenshot_available",
                },
            )

        try:
            try:
                width, height = image.size
                png_bytes = _image_to_png_bytes(image)
            except Exception as exc:
                return SkillOutput(
                    content=f"Failed to prepare screenshot for model input: {exc}",
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "screenshot_encode_failed",
                    },
                )

            request = ModelRequest(
                messages=(
                    ModelMessage(
                        role="user",
                        content=(
                            ModelTextPart(text=question),
                            ModelImagePart(data=png_bytes, media_type="image/png"),
                        ),
                    ),
                ),
                system_instruction=self._system_instruction,
                parameters=dict(self._model_parameters),
                metadata={"capability": "multimodal"},
            )

            try:
                provider = self._router.route(request)
            except ModelRoutingError as exc:
                return SkillOutput(
                    content=(
                        "Multimodal visual understanding is unavailable: "
                        f"{exc}. No configured provider supports image input."
                    ),
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "multimodal_provider_unavailable",
                    },
                )

            provider_caps = getattr(getattr(provider, "info", None), "capabilities", {}) or {}
            if not (
                provider_caps.get("multimodal")
                or provider_caps.get("image")
            ):
                return SkillOutput(
                    content=(
                        "Configured model provider does not support multimodal "
                        "image input for visual understanding."
                    ),
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "provider_lacks_image_support",
                    },
                )

            try:
                response = provider.invoke(request)
            except Exception as exc:
                return SkillOutput(
                    content=f"Visual understanding model request failed: {exc}",
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "model_request_failed",
                    },
                )

            if not response.success:
                return SkillOutput(
                    content=response.error or "Visual understanding model returned failure",
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "invalid_model_response",
                    },
                )

            content = (response.content or "").strip()
            if not content:
                return SkillOutput(
                    content="Visual understanding produced an empty response",
                    success=False,
                    metadata={
                        **auth_meta,
                        "error": "empty_response",
                    },
                )

            # Safe textual metadata only — never include PIL/image bytes.
            metadata: dict[str, Any] = {
                **auth_meta,
                "model": response.model,
                "provider": response.provider,
                "intent": intent,
                "width": width,
                "height": height,
                "format": "PNG",
                # Conservative: do not treat arbitrary screen descriptions as durable memory.
                "persist_memory": False,
            }
            usage = (response.metadata or {}).get("usage")
            if usage is not None:
                metadata["usage"] = usage

            return SkillOutput(
                content=content,
                success=True,
                metadata=metadata,
            )
        finally:
            # Keep screenshots ephemeral after visual analysis.
            clear_latest_capture()


@dataclass(slots=True)
class ScreenTaskHandler:
    """Dispatches screen/vision task inputs to concrete skills."""

    screenshot_skill: ScreenshotSkill | None = None
    region_screenshot_skill: RegionScreenshotSkill | None = None
    ocr_skill: OCRSkill | None = None
    region_ocr_skill: RegionOCRSkill | None = None
    visual_understanding_skill: VisualUnderstandingSkill | None = None
    model_router: ModelRouter | None = None

    def __post_init__(self) -> None:
        if self.screenshot_skill is None:
            self.screenshot_skill = ScreenshotSkill()
        if self.region_screenshot_skill is None:
            self.region_screenshot_skill = RegionScreenshotSkill()
        if self.ocr_skill is None:
            self.ocr_skill = OCRSkill()
        if self.region_ocr_skill is None:
            self.region_ocr_skill = RegionOCRSkill()
        if self.visual_understanding_skill is None and self.model_router is not None:
            self.visual_understanding_skill = VisualUnderstandingSkill(
                model_router=self.model_router
            )

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for this intent."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        if intent in _VISUAL_UNDERSTANDING_INTENTS:
            return SCREEN_OPERATIONS[ScreenAction.VISUAL_UNDERSTANDING].to_metadata()
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

        if intent in _VISUAL_UNDERSTANDING_INTENTS:
            if self.visual_understanding_skill is None:
                return TaskOutput(
                    content=(
                        "Visual understanding is unavailable: no multimodal "
                        "model router is configured."
                    ),
                    success=False,
                    metadata={
                        **SCREEN_OPERATIONS[ScreenAction.VISUAL_UNDERSTANDING].to_metadata(),
                        "error": "multimodal_provider_unavailable",
                    },
                )
            return self.visual_understanding_skill.run(skill_input).to_task_output()

        return TaskOutput(
            content=f"unsupported screen capability intent: '{task_input.intent}'",
            success=False,
            metadata={"error": "unsupported_capability"},
        )
