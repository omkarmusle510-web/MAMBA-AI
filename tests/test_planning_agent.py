"""Tests for PlanningAgent and AgentPlanner."""

from __future__ import annotations

import json
import pytest

from agents.planner import AgentPlanner
from agents.planning_agent import PlanningAgent, _build_user_message, _parse_plan_json, _validate_and_build_plan
from agents.types import AgentInput
from core.context import ExecutionContext
from core.errors import PlanningError
from core.types import ExecutionPlan, Observation, PlanStep, UserRequest
from models.router import DefaultModelRouter
from models.types import ModelInfo, ModelRequest, ModelResponse


class MockProvider:
    def __init__(
        self,
        name: str,
        response_content: str = "",
        success: bool = True,
        fail_with: Exception | None = None,
    ) -> None:
        self.name = name
        self.response_content = response_content
        self.success = success
        self.fail_with = fail_with

    @property
    def info(self) -> ModelInfo:
        return ModelInfo(provider=self.name, model="test-model")

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if self.fail_with is not None:
            raise self.fail_with
        return ModelResponse(
            content=self.response_content,
            provider=self.name,
            model="test-model",
            success=self.success,
        )


def test_parse_plan_json_clean_json():
    content = '{"steps": [{"description": "read file", "intent": "read_file", "metadata": {"path": "a.txt"}}]}'
    parsed = _parse_plan_json(content)
    assert "steps" in parsed
    assert len(parsed["steps"]) == 1


def test_parse_plan_json_with_markdown_fences():
    content = """```json
{
  "steps": [
    {"description": "list directory", "intent": "list_dir", "metadata": {}}
  ],
  "needs_replanning": true
}
```"""
    parsed = _parse_plan_json(content)
    assert len(parsed["steps"]) == 1
    assert parsed.get("needs_replanning") is True


def test_parse_plan_json_with_surrounding_text():
    content = """Here is the plan you requested:
{"steps": [{"description": "check status", "intent": "system_info", "metadata": {}}]}
Hope this helps!"""
    parsed = _parse_plan_json(content)
    assert len(parsed["steps"]) == 1
    assert parsed["steps"][0]["intent"] == "system_info"


def test_build_user_message_includes_memories_and_observations():
    req = UserRequest(
        goal="Diagnose system health",
        metadata={"retrieved_memories": ["Database runs on port 5432", "Memory limit is 16GB"]},
    )
    ctx = ExecutionContext.from_request(req)
    plan = ExecutionPlan(steps=(PlanStep(description="step1", intent="step1"),))
    ctx.attach_plan(plan)
    ctx.add_observation(Observation(step_id="s1", content="CPU at 98%", success=False))
    ctx.add_observation(Observation(step_id="s2", content="Disk free 200GB", success=True))

    msg = _build_user_message(AgentInput.from_context(ctx))
    assert "Goal: Diagnose system health" in msg
    assert "Database runs on port 5432" in msg
    assert "Memory limit is 16GB" in msg
    assert "[- [failed] CPU at 98%]" in msg or "[failed] CPU at 98%" in msg
    assert "[succeeded] Disk free 200GB" in msg


def test_planning_agent_produces_valid_plan():
    plan_json = json.dumps({
        "steps": [
            {"description": "read config", "intent": "read_file", "metadata": {"path": "config.yaml"}},
            {"description": "synthesize report", "intent": "respond", "metadata": {}},
        ],
        "needs_replanning": False,
    })
    provider = MockProvider("p1", response_content=plan_json)
    router = DefaultModelRouter([provider])
    agent = PlanningAgent(router=router)

    ctx = ExecutionContext.from_request(UserRequest(goal="Read config and report"))
    output = agent.reason(AgentInput.from_context(ctx))

    assert output.success is True
    assert output.plan is not None
    assert len(output.plan.steps) == 2
    assert output.plan.steps[0].intent == "read_file"
    assert output.plan.steps[1].intent == "respond"


def test_planning_agent_fallback_to_backup_on_provider_error():
    plan_json = json.dumps({
        "steps": [{"description": "ping", "intent": "system_info", "metadata": {}}],
    })
    failing = MockProvider("primary", fail_with=RuntimeError("500 Internal Error"))
    backup = MockProvider("backup", response_content=plan_json)
    router = DefaultModelRouter([failing, backup])
    agent = PlanningAgent(router=router)

    ctx = ExecutionContext.from_request(UserRequest(goal="Ping system"))
    output = agent.reason(AgentInput.from_context(ctx))

    assert output.success is True
    assert output.plan is not None
    assert len(output.plan.steps) == 1


def test_agent_planner_adapter_translates_plan_and_raises_on_failure():
    plan_json = json.dumps({
        "steps": [{"description": "list files", "intent": "list_directory", "metadata": {}}],
    })
    provider = MockProvider("p1", response_content=plan_json)
    agent = PlanningAgent(router=DefaultModelRouter([provider]))
    planner = AgentPlanner(handler=agent)

    ctx = ExecutionContext.from_request(UserRequest(goal="List files"))
    plan = planner.plan(ctx)
    assert len(plan.steps) == 1
    assert plan.steps[0].intent == "list_directory"

    # Test error path
    failing_provider = MockProvider("p1", fail_with=RuntimeError("Network down"))
    failing_agent = PlanningAgent(router=DefaultModelRouter([failing_provider]))
    failing_planner = AgentPlanner(handler=failing_agent)
    with pytest.raises(PlanningError) as exc_info:
        failing_planner.plan(ctx)
    assert "Network down" in str(exc_info.value)

