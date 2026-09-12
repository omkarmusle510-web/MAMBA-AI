"""Focused regression tests for permission behavior, approval continuation, and speech normalization."""

from __future__ import annotations

from typing import Any
import pytest

from core.brain import Brain, PendingApproval
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus, UserRequest
from permissions.policy import DefaultPermissionPolicy
from permissions.types import PermissionDecision, RiskLevel
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tools.terminal.types import classify_terminal_command
from voice.normalization import normalize_speech_text


class StaticPlanner:
    """Deterministic test planner that returns predefined plans."""

    def __init__(self, plans: list[ExecutionPlan]) -> None:
        self._plans = list(plans)
        self.call_count = 0

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        self.call_count += 1
        if self._plans:
            return self._plans.pop(0)
        return ExecutionPlan(steps=())


class RecordingHandler:
    """Mock handler that records executed steps."""

    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.executed: list[TaskInput] = []
        self._metadata = metadata or {}

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        return dict(self._metadata)

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        self.executed.append(task_input)
        return TaskOutput(
            content=f"Executed {task_input.intent}: {task_input.description}",
            success=True,
            metadata={"intent": task_input.intent},
        )


# =====================================================================
# 1. Dynamic Terminal Classification Tests
# =====================================================================

def test_terminal_command_classification_read_only_git():
    """Verify read-only git subcommands are classified as LOW risk."""
    for sub in ("status", "log", "diff", "branch", "show", "tag", "remote"):
        meta = classify_terminal_command("git", [sub])
        assert meta["risk_level"] == "low"
        assert meta["destructive"] is False


def test_terminal_command_classification_destructive_git():
    """Verify destructive git commands are classified as HIGH risk and destructive."""
    meta_reset = classify_terminal_command("git", ["reset", "--hard"])
    assert meta_reset["risk_level"] == "high"
    assert meta_reset["destructive"] is True

    meta_clean = classify_terminal_command("git", ["clean", "-fd"])
    assert meta_clean["risk_level"] == "high"
    assert meta_clean["destructive"] is True

    meta_branch = classify_terminal_command("git", ["branch", "-D", "feature"])
    assert meta_branch["risk_level"] == "high"
    assert meta_branch["destructive"] is True

    meta_push = classify_terminal_command("git", ["push", "--force", "origin", "main"])
    assert meta_push["risk_level"] == "high"
    assert meta_push["destructive"] is True


def test_terminal_command_classification_system_read():
    """Verify system readout commands are classified as LOW risk."""
    for cmd in ("echo", "dir", "cat", "ls", "pwd", "whoami", "hostname"):
        meta = classify_terminal_command(cmd, [])
        assert meta["risk_level"] == "low"
        assert meta["destructive"] is False


# =====================================================================
# 2. Permission Policy: Low and Medium Risk Automatic Execution
# =====================================================================

def test_low_and_medium_risk_actions_execute_without_asking():
    """Verify LOW and ordinary MEDIUM risk actions execute automatically."""
    handler = RecordingHandler(metadata={"action": "write_file", "risk_level": "medium", "destructive": False})
    executor = TaskExecutor(handlers={"write_file": handler})

    plan = ExecutionPlan(steps=(
        PlanStep(description="write config file", intent="write_file", metadata={"path": "app.json"}),
    ))
    planner = StaticPlanner([plan])
    brain = Brain(planner=planner, executor=executor, permissions=DefaultPermissionPolicy())

    res = brain.run("Create application configuration")
    assert res.status == ResultStatus.COMPLETED
    assert len(handler.executed) == 1
    assert "write config file" in handler.executed[0].description


# =====================================================================
# 3. High-Risk Action Confirmation & Approval Continuation
# =====================================================================

def test_high_risk_action_requires_approval_and_continues_on_yes():
    """Verify HIGH risk action pauses for approval, and resumes on user 'yes'."""
    handler = RecordingHandler(metadata={"action": "delete_file", "risk_level": "high", "destructive": True})
    executor = TaskExecutor(handlers={"delete_file": handler})

    plan = ExecutionPlan(steps=(
        PlanStep(description="delete production database", intent="delete_file", metadata={"path": "prod.db"}),
    ))
    planner = StaticPlanner([plan])
    brain = Brain(planner=planner, executor=executor, permissions=DefaultPermissionPolicy())

    # Step 1: Initial call should pause and request confirmation
    res1 = brain.run("delete prod.db")
    assert len(handler.executed) == 0  # Not executed yet!
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in res1.output

    # Step 2: User responds with "yes"
    res2 = brain.run("yes")
    assert res2.status == ResultStatus.COMPLETED
    assert len(handler.executed) == 1  # Executed after confirmation!
    assert brain._pending_approval is None
    assert "Executed delete_file" in res2.output


def test_natural_approval_phrases():
    """Verify different natural approval phrases ('approved', 'go ahead', 'sure', 'do it')."""
    for phrase in ("approved", "go ahead", "sure", "do it", "ok", "proceed"):
        handler = RecordingHandler(metadata={"action": "delete_file", "risk_level": "high", "destructive": True})
        executor = TaskExecutor(handlers={"delete_file": handler})
        plan = ExecutionPlan(steps=(
            PlanStep(description="remove temp cache", intent="delete_file", metadata={"path": "cache/"}),
        ))
        brain = Brain(planner=StaticPlanner([plan]), executor=executor, permissions=DefaultPermissionPolicy())

        brain.run("delete cache")
        assert len(handler.executed) == 0

        res = brain.run(phrase)
        assert res.status == ResultStatus.COMPLETED
        assert len(handler.executed) == 1


def test_denial_cancels_pending_action():
    """Verify 'no' or 'cancel' cancels pending action without executing."""
    for phrase in ("no", "cancel", "don't", "stop"):
        handler = RecordingHandler(metadata={"action": "delete_file", "risk_level": "high", "destructive": True})
        executor = TaskExecutor(handlers={"delete_file": handler})
        plan = ExecutionPlan(steps=(
            PlanStep(description="delete important file", intent="delete_file", metadata={"path": "file.txt"}),
        ))
        brain = Brain(planner=StaticPlanner([plan]), executor=executor, permissions=DefaultPermissionPolicy())

        brain.run("delete file")
        assert brain._pending_approval is not None

        res = brain.run(phrase)
        assert res.status == ResultStatus.COMPLETED
        assert len(handler.executed) == 0  # Never executed
        assert brain._pending_approval is None
        assert "cancelled" in res.output.lower()


def test_unprompted_yes_executes_nothing():
    """Verify saying 'yes' without any pending approval executes nothing and asks for context."""
    handler = RecordingHandler()
    executor = TaskExecutor(handlers={"any": handler})
    brain = Brain(planner=StaticPlanner([]), executor=executor, permissions=DefaultPermissionPolicy())

    res = brain.run("yes")
    assert res.status == ResultStatus.COMPLETED
    assert len(handler.executed) == 0
    assert "no pending action" in res.output.lower()


def test_unrelated_command_clears_stale_approval():
    """Verify issuing a new command clears any pending approval."""
    del_handler = RecordingHandler(metadata={"action": "delete_file", "risk_level": "high", "destructive": True})
    status_handler = RecordingHandler(metadata={"action": "status", "risk_level": "low", "destructive": False})
    executor = TaskExecutor(handlers={"delete_file": del_handler, "status": status_handler})

    plan_del = ExecutionPlan(steps=(PlanStep(description="delete file", intent="delete_file", metadata={}),))
    plan_status = ExecutionPlan(steps=(PlanStep(description="check status", intent="status", metadata={}),))

    planner = StaticPlanner([plan_del, plan_status])
    brain = Brain(planner=planner, executor=executor, permissions=DefaultPermissionPolicy())

    brain.run("delete something")
    assert brain._pending_approval is not None

    # User issues a different goal instead of answering yes/no
    res = brain.run("check status")
    assert res.status == ResultStatus.COMPLETED
    assert brain._pending_approval is None
    assert len(del_handler.executed) == 0
    assert len(status_handler.executed) == 1


def test_prior_turn_context_propagation():
    """Verify prior turn context is passed to the next turn's request metadata."""
    handler = RecordingHandler()
    executor = TaskExecutor(handlers={"echo": handler})

    plan1 = ExecutionPlan(steps=(PlanStep(description="first step", intent="echo", metadata={}),))
    plan2 = ExecutionPlan(steps=(PlanStep(description="second step", intent="echo", metadata={}),))

    planner = StaticPlanner([plan1, plan2])
    brain = Brain(planner=planner, executor=executor)

    brain.run("Run first step")
    assert brain._last_turn_context is not None
    assert brain._last_turn_context["goal"] == "Run first step"

    req2 = UserRequest(goal="Run second step")
    brain.run(req2)
    assert req2.metadata.get("prior_turn") is not None
    assert req2.metadata["prior_turn"]["goal"] == "Run first step"


# =====================================================================
# 4. Speech Normalization Tests
# =====================================================================

def test_speech_normalization_strips_markdown():
    """Verify normalize_speech_text strips markdown formatting while keeping semantic tokens."""
    md_text = """
### System Report
- Status: **Online**
- Version: *v2.1.0*
- Path: `C:\\Users\\Default\\file.txt`
- Check [Documentation](https://example.com/docs) for details.
- Math: 3 + 4 = 7, with 20% speedup.
"""
    spoken = normalize_speech_text(md_text)

    # Markdown formatting should be removed
    assert "###" not in spoken
    assert "**" not in spoken
    assert "Online" in spoken
    assert "v2.1.0" in spoken
    assert "`" not in spoken
    assert "[" not in spoken
    assert "]" not in spoken
    assert "(https://" not in spoken
    assert "Documentation" in spoken

    # Semantic tokens should be preserved
    assert "3 + 4 = 7" in spoken
    assert "20%" in spoken
    assert "C:\\Users\\Default\\file.txt" in spoken


def test_desktop_actions_execute_without_approval():
    """Verify window close/focus and clipboard read execute automatically without confirmation."""
    from tools.desktop.types import DESKTOP_OPERATIONS, DesktopAction

    close_meta = DESKTOP_OPERATIONS[DesktopAction.CLOSE_WINDOW].to_metadata()
    assert close_meta["risk_level"] == RiskLevel.LOW
    assert close_meta["destructive"] is False

    focus_meta = DESKTOP_OPERATIONS[DesktopAction.FOCUS_WINDOW].to_metadata()
    assert focus_meta["risk_level"] == RiskLevel.LOW
    assert focus_meta["destructive"] is False

    handler = RecordingHandler(metadata=close_meta)
    executor = TaskExecutor(handlers={"close_window": handler})
    plan = ExecutionPlan(steps=(
        PlanStep(description="close notepad", intent="close_window", metadata={"title": "Notepad"}),
    ))
    brain = Brain(planner=StaticPlanner([plan]), executor=executor, permissions=DefaultPermissionPolicy())

    res = brain.run("close Notepad window")
    assert res.status == ResultStatus.COMPLETED
    assert len(handler.executed) == 1


def test_destructive_git_in_brain_requires_approval_and_resumes():
    """Verify git reset --hard in Brain via terminal handler requires approval and resumes on 'yes'."""
    from skills.terminal import TerminalTaskHandler
    from skills.types import SkillOutput

    class DummyTerminalSkill:
        def __init__(self):
            self.executed = []

        def _extract_arguments(self, meta):
            return {"executable": "git", "args": ("reset", "--hard")}

        def run(self, skill_input):
            self.executed.append(skill_input)
            return SkillOutput(content="HEAD is now at a1b2c3d", success=True)

    dummy_skill = DummyTerminalSkill()
    term_handler = TerminalTaskHandler(terminal_skill=dummy_skill)
    executor = TaskExecutor(handlers={"execute_command": term_handler})

    plan = ExecutionPlan(steps=(
        PlanStep(description="git reset hard", intent="execute_command", metadata={"command": "git reset --hard"}),
    ))
    brain = Brain(planner=StaticPlanner([plan]), executor=executor, permissions=DefaultPermissionPolicy())

    # Step 1: initial run pauses for approval
    res1 = brain.run("reset git hard")
    assert len(dummy_skill.executed) == 0
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in res1.output

    # Step 2: 'yes' resumes and executes
    res2 = brain.run("yes")
    assert res2.status == ResultStatus.COMPLETED
    assert len(dummy_skill.executed) == 1
    assert brain._pending_approval is None
    assert "HEAD is now at" in res2.output


def test_high_risk_action_asks_only_once():
    """Verify a high risk action asks confirmation exactly once and does not repeat prompt."""
    handler = RecordingHandler(metadata={"action": "delete_file", "risk_level": "high", "destructive": True})
    executor = TaskExecutor(handlers={"delete_file": handler})

    plan = ExecutionPlan(steps=(
        PlanStep(description="delete log", intent="delete_file", metadata={"path": "app.log"}),
    ))
    brain = Brain(planner=StaticPlanner([plan]), executor=executor, permissions=DefaultPermissionPolicy())

    # 1. Ask once
    res1 = brain.run("delete app.log")
    assert "Action requires user confirmation" in res1.output
    assert len(handler.executed) == 0

    # 2. Approved -> executes immediately without re-prompting
    res2 = brain.run("yes")
    assert res2.status == ResultStatus.COMPLETED
    assert "Action requires user confirmation" not in (res2.output or "")
    assert len(handler.executed) == 1


def test_code_block_and_table_normalization():
    """Verify fenced code blocks and table formatting normalize cleanly for voice speech."""
    text = """
Here is the code:
```python
x = 10
y = 20
print(x + y)
```
And here is the summary table:
| Metric | Value |
| --- | --- |
| CPU | 15% |
| Memory | 4.2 GB |
"""
    spoken = normalize_speech_text(text)
    assert "```" not in spoken
    assert "|" not in spoken
    assert "---" not in spoken
    assert "CPU, Value" in spoken or "CPU" in spoken
    assert "15%" in spoken
    assert "4.2 GB" in spoken
    assert "print(x + y)" in spoken

