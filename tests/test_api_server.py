"""Focused tests for Mamba Transport Adapter (FastAPI HTTP gateway)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.server import create_app
from core.brain import Brain
from core.context import ExecutionContext
from core.runtime import MambaRuntime
from core.types import ExecutionPlan, PlanStep, ResultStatus
from permissions.policy import DefaultPermissionPolicy
from permissions.types import RiskLevel
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tests.test_core_lifecycle import EchoTaskHandler, StaticPlanner


def test_chat_endpoint_delegates_to_runtime(tmp_path: Path):
    """Verify HTTP POST /api/chat delegates directly to MambaRuntime.run() and returns result."""
    step = PlanStep(description="Say hello", intent="greet")
    plan = ExecutionPlan(steps=(step,))
    planner = StaticPlanner([plan])

    handler = EchoTaskHandler()
    executor = TaskExecutor(handlers={"greet": handler})
    brain = Brain(planner=planner, executor=executor)
    runtime = MambaRuntime(brain=brain)

    app = create_app(runtime, settings_path=tmp_path / "settings.json", reminders_path=tmp_path / "reminders.json")
    client = TestClient(app)

    response = client.post("/api/chat", json={"input": "Say hello"})
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "completed"
    assert "Executed greet: Say hello" in data["output"]
    assert data["awaiting_approval"] is False


def test_permission_ask_flow(tmp_path: Path):
    """Verify that a high-risk action yields awaiting_approval=True, and sending 'yes' resumes."""
    # Step 1: Destructive action without approved flag (yields ASK)
    step1 = PlanStep(
        description="Delete database",
        intent="delete_file",
        metadata={"path": "production.db", "risk_level": RiskLevel.HIGH},
    )
    plan1 = ExecutionPlan(steps=(step1,))

    # Handler records executions
    executed: list[str] = []

    class MockDeleteHandler:
        def run(self, task_in: TaskInput, ctx: ExecutionContext) -> TaskOutput:
            executed.append(task_in.intent)
            return TaskOutput(content="Database deleted", success=True)

    executor = TaskExecutor(handlers={"delete_file": MockDeleteHandler()})
    planner = StaticPlanner([plan1])
    brain = Brain(
        planner=planner,
        executor=executor,
        permissions=DefaultPermissionPolicy(),
    )
    runtime = MambaRuntime(brain=brain)

    app = create_app(runtime, settings_path=tmp_path / "settings.json", reminders_path=tmp_path / "reminders.json")
    client = TestClient(app)

    # 1. Trigger action requiring permission
    res1 = client.post("/api/chat", json={"input": "Delete production database"})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["awaiting_approval"] is True
    assert "user confirmation" in data1["output"].lower()
    assert len(executed) == 0  # Action not executed yet

    # 2. Approve action by sending "yes"
    res2 = client.post("/api/chat", json={"input": "yes"})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "completed"
    assert data2["awaiting_approval"] is False
    assert len(executed) == 1
