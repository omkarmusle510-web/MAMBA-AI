"""Model-backed analyze capability for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context import ExecutionContext
from models.protocols import ModelRouter
from models.types import ModelRequest
from tasks.executor import TaskExecutor
from tasks.protocols import TaskHandler
from tasks.types import TaskInput, TaskOutput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_ANALYZE_SUPPORTED_INTENTS = frozenset(
    {
        "analyze",
        "analysis",
        "calculate",
        "calculation",
        "reason",
        "reasoning",
        "compute",
        "computation",
        "evaluate",
        "eval",
        "math",
        "arithmetic",
    }
)

_DEFAULT_SYSTEM_INSTRUCTION = (
    "You are Mamba's analytical reasoning component. "
    "Analyze the given task, context, and data, perform any required reasoning, calculation, "
    "or analysis, and provide a clear, accurate, concise, and direct response."
)


def _build_analyze_prompt(input: SkillInput) -> str:
    """Convert the analyze step and execution context into a prompt."""
    task_input = input.task_input
    context = input.context

    parts: list[str] = []

    # Include user's high-level goal
    goal = task_input.goal or (context.request.goal if context and context.request else "")
    if goal:
        parts.append(f"Goal: {goal}")

    # Include step description
    if task_input.description:
        parts.append(f"Task: {task_input.description}")

    # Include step metadata if meaningful
    meta = dict(task_input.step_metadata)
    meta.pop("action", None)
    meta.pop("capability", None)
    if meta:
        details: list[str] = []
        for k, v in meta.items():
            details.append(f"- {k}: {v}")
        if details:
            parts.append("Details:\n" + "\n".join(details))

    # Include relevant observations from prior steps in context
    if context and context.observations:
        obs_lines: list[str] = []
        for obs in context.observations[-5:]:
            status = "succeeded" if obs.success else "failed"
            obs_lines.append(f"- [{status}] {obs.content[:300]}")
        if obs_lines:
            parts.append("Previous step observations:\n" + "\n".join(obs_lines))

    return "\n\n".join(parts)


class AnalyzeSkill(BaseSkill):
    """Model-backed analytical reasoning skill."""

    def __init__(
        self,
        *,
        model_router: ModelRouter,
        skill: Skill | None = None,
        system_instruction: str | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> None:
        skill_obj = skill or Skill(
            name="analyze",
            description="Performs model-backed reasoning, calculation, and analysis.",
            metadata={"action": "analyze", "type": "reasoning"},
        )
        super().__init__(skill_obj)
        self._router = model_router
        self._system_instruction = system_instruction or _DEFAULT_SYSTEM_INSTRUCTION
        self._model_parameters = model_parameters or {"temperature": 0}

    def execute(self, input: SkillInput) -> SkillOutput:
        action = str(input.task_input.step_metadata.get("action") or "").strip().lower()
        raw_intent = (input.task_input.intent or "").strip().lower()
        intent = action if action in _ANALYZE_SUPPORTED_INTENTS else raw_intent

        if intent not in _ANALYZE_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )

        prompt = _build_analyze_prompt(input)
        request = ModelRequest(
            input=prompt,
            system_instruction=self._system_instruction,
            parameters=dict(self._model_parameters),
        )

        try:
            provider = self._router.route(request)
            response = provider.invoke(request)
        except Exception as exc:
            return SkillOutput(
                content=f"Analysis model invocation failed: {exc}",
                success=False,
                metadata={"error": type(exc).__name__},
            )

        if not response.success:
            return SkillOutput(
                content=response.error or "Analysis model returned failure",
                success=False,
                metadata={"error": "model_failure"},
            )

        content = (response.content or "").strip()
        if not content:
            return SkillOutput(
                content="Analysis produced an empty response",
                success=False,
                metadata={"error": "empty_response"},
            )

        metadata: dict[str, Any] = {
            "model": response.model,
            "provider": response.provider,
            "intent": intent,
        }
        if response.metadata:
            metadata.update(response.metadata)

        return SkillOutput(
            content=content,
            success=True,
            metadata=metadata,
        )


@dataclass(slots=True)
class AnalyzeTaskHandler:
    """Adapts AnalyzeSkill to the TaskHandler interface."""

    analyze_skill: AnalyzeSkill

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        skill_input = SkillInput.from_task(task_input, context)
        return self.analyze_skill.run(skill_input).to_task_output()


def create_analyze_task_executor(
    *,
    model_router: ModelRouter,
    skill: AnalyzeSkill | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to analyze capability."""
    s = skill or AnalyzeSkill(model_router=model_router)
    return TaskExecutor(handler=AnalyzeTaskHandler(analyze_skill=s))
