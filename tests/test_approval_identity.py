"""Focused tests for approval identity binding and revocation (Safety Foundation).

An approval is a fact about one exact action + bound target, captured at the
moment the user is asked. It must never transfer to a materially changed step,
must not survive a cancellation, and must not execute after it has expired.

All resources used here are in-memory test doubles; nothing touches the real
desktop or filesystem.
"""

from __future__ import annotations

from core import brain as brain_module
from core.brain import Brain, _approval_signature
from core.context import ExecutionContext
from core.types import (
    ExecutionPlan,
    PlanStep,
    ResultStatus,
    UserRequest,
)
from permissions.policy import DefaultPermissionPolicy
from tasks.executor import TaskExecutor
from tasks.types import TaskOutput


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, task_input, context):
        self.calls.append(task_input.intent)
        return TaskOutput(content=f"Ran {task_input.intent}", success=True)


class StaticPlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan

    def plan(self, context):
        return self._plan


def _brain_with_high_step(**extra_metadata):
    step = PlanStep(
        description="delete the target",
        intent="high_risk_delete",
        metadata={"risk_level": "high", "destructive": True, **extra_metadata},
    )
    plan = ExecutionPlan(steps=(step,))
    handler = RecordingHandler()
    executor = TaskExecutor(handlers={"high_risk_delete": handler})
    brain = Brain(
        planner=StaticPlanner(plan),
        executor=executor,
        permissions=DefaultPermissionPolicy(),
    )
    return brain, handler, step


def _awaiting_observations(result):
    return [o for o in result.observations if o.metadata.get("awaiting_approval")]


def test_mutated_action_target_requires_fresh_approval():
    brain, handler, step = _brain_with_high_step(path="target_a.txt")

    res1 = brain.run("delete target_a.txt")
    assert res1.status == ResultStatus.FAILED
    assert "Action requires user confirmation" in res1.output
    assert brain._pending_approval is not None
    original_signature = brain._pending_approval.signature
    assert original_signature == _approval_signature(step)

    # The step is materially changed while the user is being asked (a replan
    # or re-bind can do this). The previous ask no longer describes it.
    brain._pending_approval.step.metadata["path"] = "target_b.txt"

    res2 = brain.run("yes")
    assert handler.calls == [], "a mutated step executed under the old approval"
    assert res2.status == ResultStatus.FAILED
    assert "Action requires user confirmation" in res2.output
    assert brain._pending_approval is not None
    assert brain._pending_approval.signature != original_signature

    res3 = brain.run("yes")
    assert res3.status == ResultStatus.COMPLETED
    assert handler.calls == ["high_risk_delete"]


def test_bound_window_identity_is_part_of_the_approval():
    brain, handler, step = _brain_with_high_step(hwnd=1001, target_pid=777)

    res1 = brain.run("close the bound window")
    assert brain._pending_approval is not None
    assert brain._pending_approval.signature == _approval_signature(step)

    # The bound window changes between ask and confirmation (re-bind).
    brain._pending_approval.step.metadata["hwnd"] = 2002
    brain._pending_approval.step.metadata["target_pid"] = 888

    res2 = brain.run("yes")
    assert handler.calls == []
    assert brain._pending_approval is not None
    assert brain._pending_approval.signature[-1] == "2002|888"

    awaiting = _awaiting_observations(res2)
    assert awaiting, "re-ask did not surface an awaiting-approval observation"
    assert awaiting[-1].metadata.get("hwnd") == 2002
    assert awaiting[-1].metadata.get("target_pid") == 888

    res3 = brain.run("yes")
    assert res3.status == ResultStatus.COMPLETED
    assert handler.calls == ["high_risk_delete"]


def test_reasked_approval_does_not_resurrect_earlier_grants():
    brain, handler, step = _brain_with_high_step(path="target_a.txt")

    brain.run("delete target_a.txt")
    assert brain._pending_approval is not None

    brain._pending_approval.step.metadata["path"] = "target_b.txt"
    brain.run("yes")
    assert handler.calls == []
    assert brain._pending_approval is not None

    # The action mutates back to the originally approved target, but that
    # earlier approval was voided by the re-ask; it must be asked for again.
    brain._pending_approval.step.metadata["path"] = "target_a.txt"
    res3 = brain.run("yes")
    assert handler.calls == []
    assert "Action requires user confirmation" in res3.output
    assert brain._pending_approval is not None

    res4 = brain.run("yes")
    assert res4.status == ResultStatus.COMPLETED
    assert handler.calls == ["high_risk_delete"]


def test_ask_observation_carries_reason_and_bound_target():
    brain, handler, _step = _brain_with_high_step(hwnd=1001, target_pid=777)

    res1 = brain.run("close the bound window")
    awaiting = _awaiting_observations(res1)
    assert awaiting
    meta = awaiting[-1].metadata
    assert meta.get("reason")
    assert meta.get("hwnd") == 1001
    assert meta.get("target_pid") == 777
    assert res1.status == ResultStatus.FAILED


def test_revoke_pending_approval_blocks_later_confirmation():
    brain, handler, _step = _brain_with_high_step(path="victim.txt")

    brain.run("delete victim.txt")
    assert brain._pending_approval is not None

    brain.revoke_pending_approval()

    assert brain._pending_approval is None
    assert brain._approved_step_ids == set()
    assert brain._approved_signatures == set()

    res2 = brain.run("yes")
    assert handler.calls == []
    assert "no pending action" in res2.output.lower()


def test_cancellation_revokes_pending_approval():
    brain, handler, _step = _brain_with_high_step(path="victim.txt")

    brain.run("delete victim.txt")
    assert brain._pending_approval is not None

    context = ExecutionContext.from_request(UserRequest(goal="unrelated"))
    result = brain._cancel_result(context, None)

    assert result.status == ResultStatus.CANCELLED
    assert brain._pending_approval is None
    assert brain._approved_step_ids == set()
    assert brain._approved_signatures == set()

    res2 = brain.run("yes")
    assert "no pending action" in res2.output.lower()
    assert handler.calls == []


def test_expired_approval_is_discarded_not_executed(monkeypatch):
    brain, handler, _step = _brain_with_high_step(path="victim.txt")

    brain.run("delete victim.txt")
    assert brain._pending_approval is not None

    monkeypatch.setattr(brain_module, "_APPROVAL_TTL_SECONDS", 0.0)

    res2 = brain.run("yes")
    assert handler.calls == [], "an expired approval executed"
    assert brain._pending_approval is None
    assert res2.status == ResultStatus.COMPLETED
    assert "expired and was discarded" in res2.output
    assert any(o.metadata.get("approval_expired") for o in res2.observations)

    res3 = brain.run("yes")
    assert "no pending action" in res3.output.lower()
    assert handler.calls == []
