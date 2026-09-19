"""Tests for multi-step compound workflow continuation and replanning loop fixes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, ExecutionResult, Observation, PlanStep, ResultStatus, UserRequest
from permissions.types import PermissionDecision, PermissionRequest, PermissionResult
from skills.filesystem import (
    CreateDirectorySkill,
    DeleteSkill,
    FilesystemTaskHandler,
    ListDirectorySkill,
    ReadFileSkill,
    WriteFileSkill,
)
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput


class SequencePlanner:
    """Planner that returns a sequence of execution plans."""

    def __init__(self, plans: list[ExecutionPlan]) -> None:
        self._plans = list(plans)
        self.call_count = 0
        self.contexts_seen: list[ExecutionContext] = []

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        self.call_count += 1
        self.contexts_seen.append(context)
        if self._plans:
            return self._plans.pop(0)
        return ExecutionPlan(steps=())


class AllowAllPermissionPolicy:
    """Test permission policy that allows all operations."""

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        return PermissionResult(decision=PermissionDecision.ALLOW, reason="allowed by test policy", request=request)


def test_compound_create_write_read_delete_workflow(tmp_path: Path):
    """Verify that a 4-step compound plan completes successfully without identical-plan failure."""
    fs_handler = FilesystemTaskHandler(
        list_directory_skill=ListDirectorySkill(root_dir=tmp_path),
        read_file_skill=ReadFileSkill(root_dir=tmp_path),
        write_file_skill=WriteFileSkill(root_dir=tmp_path),
        create_directory_skill=CreateDirectorySkill(root_dir=tmp_path),
        delete_skill=DeleteSkill(root_dir=tmp_path),
    )
    executor = TaskExecutor(handlers={
        "create_file": fs_handler,
        "write_file": fs_handler,
        "read_file": fs_handler,
        "delete_file": fs_handler,
    })

    target_file = tmp_path / "lifecycle.txt"

    step1 = PlanStep(
        id="s1",
        description="create lifecycle file",
        intent="create_file",
        metadata={"path": str(target_file)},
    )
    step2 = PlanStep(
        id="s2",
        description="write greeting",
        intent="write_file",
        metadata={"path": str(target_file), "content": "Hello Mamba!"},
    )
    step3 = PlanStep(
        id="s3",
        description="read file back",
        intent="read_file",
        metadata={"path": str(target_file)},
    )
    step4 = PlanStep(
        id="s4",
        description="delete file",
        intent="delete_file",
        metadata={"path": str(target_file)},
    )

    # Initial plan has needs_replanning: True (common for LLMs)
    plan1 = ExecutionPlan(
        steps=(step1, step2, step3, step4),
        metadata={"needs_replanning": True},
    )
    # If a second cycle was invoked, a model might emit the same plan
    plan2 = ExecutionPlan(
        steps=(step1, step2, step3, step4),
        metadata={"needs_replanning": False},
    )

    planner = SequencePlanner([plan1, plan2])
    brain = Brain(
        planner=planner,
        executor=executor,
        permissions=AllowAllPermissionPolicy(),
    )

    result = brain.run("Create, write, read, and delete lifecycle.txt")

    assert result.status == ResultStatus.COMPLETED
    # The file should be cleaned up at the end
    assert not target_file.exists()


def test_replanning_with_completed_steps_aware():
    """Verify that when a step fails mid-workflow, completed steps are tracked and filtered."""
    execution_history: list[str] = []

    class MockStepHandler:
        def __init__(self):
            self.attempt = 0

        def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
            execution_history.append(f"{task_input.intent}:{task_input.description}")
            if task_input.intent == "step2" and self.attempt == 0:
                self.attempt += 1
                return TaskOutput(content="network timeout", success=False, metadata={"replan": True})
            return TaskOutput(content=f"success {task_input.intent}", success=True)

    handler = MockStepHandler()
    executor = TaskExecutor(handlers={"step1": handler, "step2": handler, "step3": handler})

    s1 = PlanStep(id="s1", description="first action", intent="step1")
    s2 = PlanStep(id="s2", description="second action", intent="step2")
    s3 = PlanStep(id="s3", description="third action", intent="step3")

    # Cycle 1 plans all 3 steps; s1 succeeds, s2 fails
    plan1 = ExecutionPlan(steps=(s1, s2, s3))
    # Cycle 2 planner re-emits s1, s2, s3 (or recovery)
    plan2 = ExecutionPlan(steps=(s1, s2, s3))

    planner = SequencePlanner([plan1, plan2])
    brain = Brain(planner=planner, executor=executor)

    result = brain.run("Run three steps with recovery")

    assert result.status == ResultStatus.COMPLETED
    assert planner.call_count == 2
    # In cycle 2, context captured the actual failure observation
    ctx2 = planner.contexts_seen[1]
    assert any("network timeout" in str(obs.content) for obs in ctx2.observations)
    # Context also records completed steps
    completed_meta = ctx2.record.request.metadata.get("completed_steps", [])
    assert any(s.get("intent") == "step1" for s in completed_meta)

    # Step 1 should only be executed ONCE (in cycle 1), not re-executed in cycle 2!
    step1_runs = [h for h in execution_history if h.startswith("step1:")]
    assert len(step1_runs) == 1
    # Step 2 ran twice (failed once, then succeeded)
    step2_runs = [h for h in execution_history if h.startswith("step2:")]
    assert len(step2_runs) == 2


def test_identical_failed_plan_stops_cleanly():
    """Verify that a step repeatedly failing with no progress stops cleanly as FAILED."""
    class AlwaysFailHandler:
        def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
            return TaskOutput(content="permanent error", success=False, metadata={"replan": True})

    executor = TaskExecutor(handlers={"fail_action": AlwaysFailHandler()})

    s_fail = PlanStep(id="sf", description="failing step", intent="fail_action", metadata={"path": "bad"})

    plan1 = ExecutionPlan(steps=(s_fail,))
    plan2 = ExecutionPlan(steps=(s_fail,))
    plan3 = ExecutionPlan(steps=(s_fail,))

    planner = SequencePlanner([plan1, plan2, plan3])
    brain = Brain(planner=planner, executor=executor)

    result = brain.run("Attempt impossible task")

    assert result.status == ResultStatus.FAILED
    err_msg = str(result.error or result.output or "")
    assert "pointless loop" in err_msg or "identical plan" in err_msg


def test_changed_recovery_plan_is_allowed():
    """Verify that when a step fails, a genuinely changed recovery plan executes successfully."""
    attempts: list[str] = []

    class RecoveryHandler:
        def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
            attempts.append(f"{task_input.intent}:{task_input.description}")
            if task_input.intent == "bad_action":
                return TaskOutput(content="bad parameter error", success=False, metadata={"replan": True})
            return TaskOutput(content="recovered successfully", success=True)

    executor = TaskExecutor(handlers={"bad_action": RecoveryHandler(), "recovered_action": RecoveryHandler()})

    # Cycle 1: bad step fails
    s_bad = PlanStep(id="s_bad", description="try bad action", intent="bad_action")
    plan1 = ExecutionPlan(steps=(s_bad,))

    # Cycle 2: planner sees failure, plans genuinely different recovery action
    s_good = PlanStep(id="s_good", description="try corrected recovery action", intent="recovered_action")
    plan2 = ExecutionPlan(steps=(s_good,))

    planner = SequencePlanner([plan1, plan2])
    brain = Brain(planner=planner, executor=executor)

    result = brain.run("Perform task with recovery")

    assert result.status == ResultStatus.COMPLETED
    assert planner.call_count == 2
    assert attempts == ["bad_action:try bad action", "recovered_action:try corrected recovery action"]

