"""Model-backed planning agent for Mamba.

Connects a real LLM (via ModelRouter) to Mamba's existing Agent abstraction.
The agent sends the user's goal and execution context to the model,
receives structured JSON planning output, validates it, and converts it
into the existing ExecutionPlan contract.
"""

from __future__ import annotations

import json
from typing import Any

from core.types import ExecutionPlan, PlanStep

from models.protocols import ModelRouter
from models.types import ModelRequest

from .errors import AgentPlanningError, AgentReasoningError
from .types import Agent, AgentInput, AgentOutput

_SYSTEM_PROMPT = """\
You are Mamba's planning component.

Your job: given a user goal and execution context, produce a structured \
execution plan as a JSON object. You are planning actions for Mamba to \
execute — you are not executing them yourself.

Rules:
- Return ONLY a single JSON object, no markdown, no commentary.
- The JSON must have this exact structure:
  {"steps": [{"description": "...", "intent": "...", "metadata": {}}], \
"needs_replanning": false}
- "description": a clear, concise description of what this step does.
- "intent": a short action verb or phrase (e.g. "inspect", "read_file", \
"list_directory", "run_command", "search", "analyze").
- "metadata": an object with any additional key-value context for the step \
(can be empty {}).
- Steps must be grounded in the user's request. Do not invent capabilities \
that do not exist.
- Do not claim actions have already been performed.
- Keep plans focused and minimal — only the steps genuinely needed.
- If the goal is unclear, produce a single clarification step with \
intent "clarify".
- "needs_replanning": set this to true only when you already know, right \
now, that this plan's steps will not be enough on their own — for example \
a later action depends on information a step in this same plan hasn't \
discovered yet (e.g. reading a file whose name a directory listing will \
reveal). In that case, plan only the steps you can already justify and \
set "needs_replanning": true; you will be shown the resulting \
observations and asked to plan the rest afterward. For an ordinary \
request whose steps you can fully specify now, omit this field or set it \
to false — the plan will be considered complete once those steps finish, \
with no extra step or call required.
"""


def _build_user_message(input: AgentInput) -> str:
    """Build a compact user message from the execution context."""
    ctx = input.context
    goal = ctx.request.goal
    parts = [f"Goal: {goal}"]

    # Include request metadata if present (e.g. retrieved memories).
    req_meta = ctx.request.metadata
    if req_meta.get("retrieved_memories"):
        memories = req_meta["retrieved_memories"]
        parts.append(f"Relevant memory: {'; '.join(str(m) for m in memories[:5])}")

    # Include prior observations if re-planning.
    observations = ctx.observations
    if observations:
        obs_lines = []
        for obs in observations[-5:]:  # Last 5 observations max.
            status = "succeeded" if obs.success else "failed"
            obs_lines.append(f"- [{status}] {obs.content[:200]}")
        parts.append("Previous observations:\n" + "\n".join(obs_lines))

    return "\n\n".join(parts)


def _parse_plan_json(content: str) -> dict[str, Any]:
    """Extract and parse the JSON plan from model output.

    Handles the common case where models wrap JSON in markdown fences.
    """
    text = content.strip()

    # Strip markdown code fences if the model added them.
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentPlanningError(
            f"model returned invalid JSON: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise AgentPlanningError(
            f"expected a JSON object, got {type(parsed).__name__}"
        )

    return parsed


def _validate_and_build_plan(raw: dict[str, Any]) -> ExecutionPlan:
    """Validate raw parsed JSON and build an ExecutionPlan."""
    # Validate steps field.
    if "steps" not in raw:
        raise AgentPlanningError("plan JSON missing required 'steps' field")

    raw_steps = raw["steps"]
    if not isinstance(raw_steps, list):
        raise AgentPlanningError(
            f"'steps' must be a list, got {type(raw_steps).__name__}"
        )
    if not raw_steps:
        raise AgentPlanningError("plan has no steps")

    # Validate and build each PlanStep.
    plan_steps: list[PlanStep] = []
    for i, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise AgentPlanningError(
                f"step {i} must be an object, got {type(raw_step).__name__}"
            )

        description = raw_step.get("description")
        if not description or not isinstance(description, str) or not description.strip():
            raise AgentPlanningError(
                f"step {i} missing or empty 'description'"
            )

        intent = raw_step.get("intent")
        if not intent or not isinstance(intent, str) or not intent.strip():
            raise AgentPlanningError(
                f"step {i} missing or empty 'intent'"
            )

        metadata = raw_step.get("metadata", {})
        if not isinstance(metadata, dict):
            raise AgentPlanningError(
                f"step {i} 'metadata' must be an object, "
                f"got {type(metadata).__name__}"
            )

        plan_steps.append(PlanStep(
            description=description.strip(),
            intent=intent.strip(),
            metadata=metadata,
        ))

    plan_metadata: dict[str, Any] = {}
    if "goal" in raw and isinstance(raw["goal"], str):
        plan_metadata["goal"] = raw["goal"]
    if raw.get("needs_replanning") is True:
        plan_metadata["needs_replanning"] = True

    return ExecutionPlan(steps=tuple(plan_steps), metadata=plan_metadata)


class PlanningAgent:
    """Concrete model-backed agent that produces ExecutionPlans.

    Uses the existing ModelRouter to select and call a ModelProvider,
    then parses the structured JSON response into a validated ExecutionPlan.

    Satisfies the AgentHandler protocol (has a .reason() method).
    """

    def __init__(
        self,
        *,
        router: ModelRouter,
        agent: Agent | None = None,
        system_prompt: str | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> None:
        self._router = router
        self._agent = agent or Agent(
            name="mamba_planner",
            description="Model-backed planning agent for Mamba",
        )
        self._system_prompt = system_prompt or _SYSTEM_PROMPT
        self._model_parameters = model_parameters or {
            "temperature": 0,
            "max_tokens": 2048,
        }

    @property
    def agent(self) -> Agent:
        return self._agent

    def reason(self, input: AgentInput) -> AgentOutput:
        """Reason about the goal and produce an ExecutionPlan.

        Flow:
            AgentInput → ModelRequest → ModelRouter → ModelProvider
            → ModelResponse → parse JSON → validate → ExecutionPlan
        """
        # 1. Build the model request.
        user_message = _build_user_message(input)
        request = ModelRequest(
            input=user_message,
            system_instruction=self._system_prompt,
            parameters=dict(self._model_parameters),
        )

        # 2. Route to a provider and invoke.
        try:
            provider = self._router.route(request)
            response = provider.invoke(request)
        except Exception as exc:
            return AgentOutput(
                success=False,
                error=f"model invocation failed: {exc}",
            )

        # 3. Check model response.
        if not response.success:
            return AgentOutput(
                success=False,
                error=f"model returned failure: {response.error}",
            )
        if not response.content or not response.content.strip():
            return AgentOutput(
                success=False,
                error="model returned empty response",
            )

        # 4. Parse structured output → ExecutionPlan.
        try:
            raw_plan = _parse_plan_json(response.content)
            plan = _validate_and_build_plan(raw_plan)
        except AgentPlanningError as exc:
            return AgentOutput(
                success=False,
                error=str(exc),
            )

        return AgentOutput(
            plan=plan,
            success=True,
            metadata={
                "model": response.model,
                "provider": response.provider,
            },
        )

