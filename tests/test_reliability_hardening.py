"""Focused validation and regression tests for Mamba AI Reliability Hardening Loop.

Covers all 12 reliability hardening items:
1. Planner JSON resilience, string repair, and 1-shot model repair.
2. Conversational referent resolution (it, that file, the file, this repository).
3. Correction and replacement commands (No, don't do that. Instead ..., Actually make that ...).
4. Extended natural approval & denial recognition.
5. Permission invariants (LOW/MEDIUM automatic, HIGH requires confirmation).
6. Precise terminal Git classification (config, tag, remote).
7. File write / overwrite safety (new file automatic, non-empty overwrite high risk).
8. Physical verification outcomes (file_exists, file_absent, content_matches).
9. GitHub URL classification (profile/org URL clarification vs repo parsing).
10. Safe desktop URL opening and terminal URL interception.
11. Voice TTS 429 quota degradation handling.
12. Voice turn recovery and state cleanliness.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from agents.planning_agent import (
    PlanningAgent,
    _parse_plan_json,
    _build_user_message,
)
from core.brain import (
    Brain,
    _is_approval_phrase,
    _is_denial_phrase,
    _REPLACEMENT_PATTERN,
    _FILENAME_REPLACEMENT_PATTERN,
)
from core.context import ExecutionContext
from core.types import (
    ExecutionPlan,
    Observation,
    PlanStep,
    ResultStatus,
    UserRequest,
)
from models.protocols import ModelProvider, ModelRouter
from models.types import ModelRequest, ModelResponse
from permissions.policy import DefaultPermissionPolicy
from permissions.types import RiskLevel
from skills.desktop import DesktopTaskHandler, OpenURLSkill
from skills.filesystem import FilesystemTaskHandler, WriteFileSkill
from skills.github import (
    GetRepositorySkill,
    _extract_github_metadata,
)
from skills.terminal import TerminalSkill, TerminalTaskHandler
from tasks.executor import TaskExecutor
from tasks.types import TaskInput
from tools.desktop.types import DESKTOP_OPERATIONS, DesktopAction
from tools.terminal.types import classify_terminal_command
from verification.types import VerificationRequest, VerificationStatus
from verification.verifier import DefaultVerifier
from voice.interface import VoiceInterface
from voice.tts import CloudflareTTSProvider


# =====================================================================
# 1. Planner JSON Reliability & 1-Shot Repair Tests
# =====================================================================

def test_parse_plan_json_with_smart_quotes_and_trailing_commas():
    """Verify _parse_plan_json handles smart quotes, markdown fences, and trailing commas."""
    raw = '''```json
    {
        “steps”: [
            {
                “description”: “Run git status”,
                “intent”: “run_command”,
                “metadata”: {“command”: “git status”,},
            },
        ],
        “needs_replanning”: false,
    }
    ```'''
    parsed = _parse_plan_json(raw)
    assert "steps" in parsed
    assert len(parsed["steps"]) == 1
    assert parsed["steps"][0]["intent"] == "run_command"


def test_parse_plan_json_with_prose_wrapping():
    """Verify _parse_plan_json extracts JSON when wrapped in prose commentary."""
    raw = '''Here is the requested plan:
    {
        "steps": [
            {"description": "List files", "intent": "list_directory", "metadata": {"path": "."}}
        ],
        "needs_replanning": false
    }
    Hope this helps!'''
    parsed = _parse_plan_json(raw)
    assert len(parsed["steps"]) == 1
    assert parsed["steps"][0]["intent"] == "list_directory"


def _make_task_input(intent: str, metadata: dict) -> TaskInput:
    return TaskInput(
        step_id="step-1",
        description="test step",
        intent=intent,
        execution_id="exec-1",
        goal="test goal",
        step_metadata=metadata,
    )


def test_planning_agent_one_shot_repair_on_malformed_json():
    """Verify PlanningAgent makes a 1-shot repair call when initial JSON parsing fails."""
    # First response: broken JSON with syntax error
    bad_response = ModelResponse(
        content='{"steps": [{"description": "Broken", "intent": "test", metadata: {}}]}',  # unquoted metadata
        model="groq/llama-3.3-70b-versatile",
        provider="groq",
        success=True,
    )
    # Repaired response: valid JSON
    repaired_response = ModelResponse(
        content='{"steps": [{"description": "Repaired step", "intent": "test", "metadata": {}}]}',
        model="groq/llama-3.3-70b-versatile",
        provider="groq",
        success=True,
    )

    mock_router = MagicMock()
    mock_router.invoke.side_effect = [bad_response, repaired_response]

    agent = PlanningAgent(router=mock_router)
    user_req = UserRequest(goal="Do something")
    ctx = ExecutionContext.from_request(user_req)
    from agents.types import AgentInput
    output = agent.reason(AgentInput(context=ctx))

    assert output.success is True
    assert output.plan is not None
    assert len(output.plan.steps) == 1
    assert output.plan.steps[0].description == "Repaired step"
    assert mock_router.invoke.call_count == 2


# =====================================================================
# 2. Conversational Referent Context Tests
# =====================================================================

def test_conversational_referent_resolution_for_files(tmp_path: Path):
    """Verify 'that file', 'the file', 'it' resolve to recent active entities across turns."""
    plan1 = ExecutionPlan(steps=(
        PlanStep(description="create file", intent="write_file", metadata={"path": "notes.txt", "content": "hello"}),
    ))
    plan2 = ExecutionPlan(steps=(
        PlanStep(description="read file", intent="read_file", metadata={"path": "notes.txt"}),
    ))

    captured_goals = []
    class InspectingPlanner:
        def __init__(self):
            self.plans = [plan1, plan2]
            self.idx = 0

        def plan(self, context):
            captured_goals.append(context.request.goal)
            p = self.plans[self.idx]
            self.idx = min(self.idx + 1, len(self.plans) - 1)
            return p

    class EchoHandler:
        def run(self, task_input, context):
            from tasks.types import TaskOutput
            return TaskOutput(content="ok", success=True, metadata=dict(task_input.step_metadata))

    executor = TaskExecutor(handlers={"write_file": EchoHandler(), "read_file": EchoHandler()})
    brain = Brain(planner=InspectingPlanner(), executor=executor)

    # Turn 1: Explicit filename
    res1 = brain.run("create notes.txt")
    assert res1.status == ResultStatus.COMPLETED
    assert brain._active_entities.get("file") == "notes.txt"

    # Turn 2: "read that file"
    res2 = brain.run("read that file")
    assert res2.status == ResultStatus.COMPLETED
    assert "read notes.txt" in captured_goals[1]

    # Turn 3: "delete it"
    res3 = brain.run("delete it")
    assert res3.status == ResultStatus.COMPLETED
    assert "delete notes.txt" in captured_goals[2]


def test_conversational_referent_resolution_for_repository():
    """Verify 'this repository' resolves to active repository entity across turns."""
    captured_goals = []
    class InspectingPlanner:
        def plan(self, context):
            captured_goals.append(context.request.goal)
            return ExecutionPlan(steps=(
                PlanStep(description="inspect repo", intent="get_repo", metadata={"repo": "owner/mamba"}),
            ))

    class DummyHandler:
        def run(self, task_input, context):
            from tasks.types import TaskOutput
            return TaskOutput(content="repo info", success=True, metadata={"repository": "owner/mamba"})

    executor = TaskExecutor(handlers={"get_repo": DummyHandler()})
    brain = Brain(planner=InspectingPlanner(), executor=executor)

    # Turn 1: GitHub URL / repo mention
    res1 = brain.run("check github.com/owner/mamba")
    assert res1.status == ResultStatus.COMPLETED
    assert brain._active_entities.get("repository") == "owner/mamba"

    # Turn 2: Referent
    res2 = brain.run("what is in this repository?")
    assert res2.status == ResultStatus.COMPLETED
    assert "owner/mamba" in captured_goals[1]


def test_conversational_referent_the_one_i_just_created():
    """Verify 'the one I just created' resolves to the active file created in previous turn."""
    captured_goals = []
    class InspectingPlanner:
        def plan(self, context):
            captured_goals.append(context.request.goal)
            return ExecutionPlan(steps=(
                PlanStep(description="dummy step", intent="echo", metadata={}),
            ))

    class DummyHandler:
        def run(self, task_input, context):
            from tasks.types import TaskOutput
            return TaskOutput(content="ok", success=True, metadata={"path": "script.py"})

    executor = TaskExecutor(handlers={"echo": DummyHandler()})
    brain = Brain(planner=InspectingPlanner(), executor=executor)

    res1 = brain.run("create script.py")
    assert res1.status == ResultStatus.COMPLETED
    assert brain._active_entities.get("file") == "script.py"

    res2 = brain.run("run the one I just created")
    assert res2.status == ResultStatus.COMPLETED
    assert "run script.py" in captured_goals[1]


def test_conversational_referent_folder_and_command():
    """Verify 'the folder' and 'the command you just ran' resolve to active entities."""
    captured_goals = []
    class InspectingPlanner:
        def plan(self, context):
            captured_goals.append(context.request.goal)
            return ExecutionPlan(steps=(
                PlanStep(description="dummy step", intent="echo", metadata={}),
            ))

    class DummyHandler:
        def run(self, task_input, context):
            from tasks.types import TaskOutput
            return TaskOutput(
                content="directory contents",
                success=True,
                metadata={"path": "src/mamba", "command": {"executable": "pytest", "args": ["-v"]}},
            )

    executor = TaskExecutor(handlers={"echo": DummyHandler()})
    brain = Brain(planner=InspectingPlanner(), executor=executor)

    res1 = brain.run("list files in src/mamba")
    assert res1.status == ResultStatus.COMPLETED
    assert brain._active_entities.get("folder") == "src/mamba"
    assert brain._active_entities.get("command") == "pytest -v"

    res2 = brain.run("what is inside that directory?")
    assert res2.status == ResultStatus.COMPLETED
    assert "src/mamba" in captured_goals[1]

    res3 = brain.run("explain the command you just ran")
    assert res3.status == ResultStatus.COMPLETED
    assert "pytest -v" in captured_goals[2]


# =====================================================================
# 3. Correction & Replacement Context Tests
# =====================================================================

def test_replacement_command_cancels_approval_and_executes_replacement():
    """Verify 'No, don't do that. Instead show me system info' cancels approval and runs replacement."""
    high_risk_step = PlanStep(
        description="delete everything",
        intent="delete_file",
        metadata={"action": "delete_file", "risk_level": "high", "destructive": True},
    )
    plan1 = ExecutionPlan(steps=(high_risk_step,))
    plan2 = ExecutionPlan(steps=(
        PlanStep(description="show system info", intent="system_info", metadata={}),
    ))

    executed_intents = []
    class Handler:
        def run(self, task_input, context):
            from tasks.types import TaskOutput
            executed_intents.append(task_input.intent)
            return TaskOutput(content="Linux 6.1", success=True)

    executor = TaskExecutor(handlers={"delete_file": Handler(), "system_info": Handler()})

    class MultiPlanner:
        def __init__(self):
            self.calls = 0
        def plan(self, context):
            self.calls += 1
            return plan1 if self.calls == 1 else plan2

    brain = Brain(planner=MultiPlanner(), executor=executor, permissions=DefaultPermissionPolicy())

    # Step 1: Pauses for approval
    res1 = brain.run("delete everything")
    assert res1.status == ResultStatus.FAILED
    assert "requires user confirmation" in res1.output

    # Step 2: User corrects and replaces intent
    res2 = brain.run("No, don't do that. Instead show me system info")
    assert res2.status == ResultStatus.COMPLETED
    assert "delete_file" not in executed_intents
    assert "system_info" in executed_intents


# =====================================================================
# 4. Extended Approval & Denial Phrase Tests
# =====================================================================

def test_extended_approval_phrases():
    """Verify natural approval variations are recognized."""
    approvals = [
        "Yes, you can proceed.",
        "yes please proceed",
        "you can proceed",
        "please proceed",
        "go ahead and do it",
        "go ahead and run it",
        "run that",
        "do that",
        "execute that",
        "sounds good",
        "that is fine",
        "that's fine",
        "sure, do it",
    ]
    for phrase in approvals:
        assert _is_approval_phrase(phrase) is True, f"Failed on: {phrase}"


def test_extended_denial_phrases():
    """Verify natural denial variations are recognized."""
    denials = [
        "cancel it",
        "cancel that",
        "don't do it",
        "dont do it",
        "don't do that",
        "dont do that",
        "stop that",
        "never mind",
        "forget it",
        "no thanks",
    ]
    for phrase in denials:
        assert _is_denial_phrase(phrase) is True, f"Failed on: {phrase}"


# =====================================================================
# 5 & 6. Terminal / Git Command Classification Tests
# =====================================================================

def test_git_config_classification():
    """Verify git config inspect is LOW risk and mutating is MEDIUM risk."""
    read_meta = classify_terminal_command("git", ["config", "--get", "user.name"])
    assert read_meta["risk_level"] == "low"
    assert read_meta["destructive"] is False

    list_meta = classify_terminal_command("git", ["config", "-l"])
    assert list_meta["risk_level"] == "low"

    write_meta = classify_terminal_command("git", ["config", "user.name", "Alice"])
    assert write_meta["risk_level"] == "medium"
    assert write_meta["destructive"] is False


def test_git_tag_classification():
    """Verify git tag listing is LOW, create is MEDIUM, and delete is HIGH risk."""
    list_meta = classify_terminal_command("git", ["tag", "-l"])
    assert list_meta["risk_level"] == "low"

    create_meta = classify_terminal_command("git", ["tag", "v1.0"])
    assert create_meta["risk_level"] == "medium"
    assert create_meta["destructive"] is False

    delete_meta = classify_terminal_command("git", ["tag", "-d", "v1.0"])
    assert delete_meta["risk_level"] == "high"
    assert delete_meta["destructive"] is True


def test_git_remote_classification():
    """Verify git remote view is LOW and modifying is MEDIUM risk."""
    view_meta = classify_terminal_command("git", ["remote", "-v"])
    assert view_meta["risk_level"] == "low"

    modify_meta = classify_terminal_command("git", ["remote", "add", "origin", "https://github.com/a/b"])
    assert modify_meta["risk_level"] == "medium"
    assert modify_meta["destructive"] is False


# =====================================================================
# 7. File Write / Overwrite Safety Tests
# =====================================================================

def test_file_write_new_file_is_medium_risk(tmp_path: Path):
    """Writing to a new file is medium risk and non-destructive."""
    skill = WriteFileSkill(root_dir=tmp_path)
    new_file = tmp_path / "new_file.txt"
    task_input = _make_task_input(intent="write_file", metadata={"path": str(new_file), "content": "test"})
    meta = skill.get_metadata(task_input)

    assert meta["risk_level"] == "medium"
    assert meta["destructive"] is False


def test_file_write_overwrite_non_empty_file_is_high_risk(tmp_path: Path):
    """Writing to an existing non-empty file is flagged as high risk & destructive."""
    existing_file = tmp_path / "existing.txt"
    existing_file.write_text("existing data", encoding="utf-8")

    skill = WriteFileSkill(root_dir=tmp_path)
    task_input = _make_task_input(intent="write_file", metadata={"path": str(existing_file), "content": "overwrite"})
    meta = skill.get_metadata(task_input)

    assert meta["risk_level"] == "high"
    assert meta["destructive"] is True


# =====================================================================
# 8. Physical Outcome Verification Tests
# =====================================================================

def test_verifier_file_exists_predicate(tmp_path: Path):
    """Verify file_exists predicate checks physical file existence."""
    verifier = DefaultVerifier()
    test_file = tmp_path / "created.txt"
    test_file.write_text("content", encoding="utf-8")

    # When file exists
    res = verifier.verify(VerificationRequest(
        expected={"file_exists": str(test_file)},
        actual="ok",
    ))
    assert res.status == VerificationStatus.VERIFIED

    # When file does not exist
    missing_file = tmp_path / "missing.txt"
    res_fail = verifier.verify(VerificationRequest(
        expected={"file_exists": str(missing_file)},
        actual="ok",
    ))
    assert res_fail.status == VerificationStatus.FAILED


def test_verifier_file_absent_predicate(tmp_path: Path):
    """Verify file_absent predicate verifies physical file absence."""
    verifier = DefaultVerifier()
    missing_file = tmp_path / "deleted.txt"

    res = verifier.verify(VerificationRequest(
        expected={"file_absent": str(missing_file)},
        actual="ok",
    ))
    assert res.status == VerificationStatus.VERIFIED

    existing = tmp_path / "still_here.txt"
    existing.write_text("stay", encoding="utf-8")
    res_fail = verifier.verify(VerificationRequest(
        expected={"file_absent": str(existing)},
        actual="ok",
    ))
    assert res_fail.status == VerificationStatus.FAILED


def test_verifier_content_matches_predicate(tmp_path: Path):
    """Verify content_matches predicate checks actual file content."""
    verifier = DefaultVerifier()
    target = tmp_path / "data.txt"
    target.write_text("expected content", encoding="utf-8")

    res = verifier.verify(VerificationRequest(
        expected={"content_matches": {"path": str(target), "content": "expected content"}},
        actual="ok",
    ))
    assert res.status == VerificationStatus.VERIFIED


# =====================================================================
# 9. GitHub URL & Context Tests
# =====================================================================

def test_github_profile_url_returns_clarification():
    """Verify a GitHub profile URL returns honest clarification instead of failed repo lookup."""
    skill = GetRepositorySkill()
    from skills.types import SkillInput
    task_input = _make_task_input(
        intent="get_repository",
        metadata={"url": "https://github.com/torvalds?tab=repositories"},
    )
    ctx = ExecutionContext.from_request(UserRequest(goal="check profile"))
    output = skill.execute(SkillInput(task_input=task_input, context=ctx))

    assert output.success is True
    assert "profile or organization" in output.content
    assert output.metadata.get("clarification_needed") is True


def test_github_repo_url_extracts_owner_and_repo():
    """Verify a repository URL is cleanly parsed into owner and repo."""
    meta = _extract_github_metadata({"url": "https://github.com/torvalds/linux.git"})
    assert meta["owner"] == "torvalds"
    assert meta["repo"] == "linux"


# =====================================================================
# 10. Desktop Open URL & Terminal Interception Tests
# =====================================================================

def test_open_url_skill():
    """Verify OpenURLSkill calls webbrowser.open safely."""
    skill = OpenURLSkill()
    from skills.types import SkillInput
    task_input = _make_task_input(intent="open_url", metadata={"url": "https://example.com"})
    ctx = ExecutionContext.from_request(UserRequest(goal="open example"))

    with patch("webbrowser.open", return_value=True) as mock_open:
        output = skill.execute(SkillInput(task_input=task_input, context=ctx))
        assert output.success is True
        assert "Opened 'https://example.com'" in output.content
        mock_open.assert_called_once_with("https://example.com")


def test_terminal_intercepts_url_open():
    """Verify TerminalSkill intercepts 'open https://...' without failing shell checks."""
    skill = TerminalSkill()
    from skills.types import SkillInput
    task_input = _make_task_input(intent="run_command", metadata={"command": "open https://mamba.ai"})
    ctx = ExecutionContext.from_request(UserRequest(goal="open site"))

    with patch("webbrowser.open", return_value=True) as mock_open:
        output = skill.execute(SkillInput(task_input=task_input, context=ctx))
        assert output.success is True
        assert "Opened 'https://mamba.ai'" in output.content
        mock_open.assert_called_once_with("https://mamba.ai")


# =====================================================================
# 11 & 12. Voice TTS 429 Quota & Recovery Tests
# =====================================================================

def test_voice_tts_429_quota_degrades_gracefully():
    """Verify Cloudflare 429 error marks session as degraded and skips repeated TTS calls."""
    mock_brain = MagicMock(spec=Brain)
    from core.types import ExecutionResult
    mock_brain.run.return_value = ExecutionResult(
        execution_id="1",
        status=ResultStatus.COMPLETED,
        goal="status",
        observations=(),
        output="System is healthy.",
    )

    mock_tts = MagicMock(spec=CloudflareTTSProvider)
    from voice.errors import TTSError
    mock_tts.synthesize.side_effect = TTSError("Cloudflare TTS HTTP 429 Quota Exceeded: 10,000 neurons exhausted")

    mock_player = MagicMock()
    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = "how are you"

    voice = VoiceInterface(
        brain=mock_brain,
        stt=mock_stt,
        tts=mock_tts,
        player=mock_player,
    )

    assert voice.tts_degraded is False

    # Turn 1: 429 triggers degradation
    text, res = voice.process_voice_input(b"audio1", speak_response=True)
    assert text == "how are you"
    assert res.status == ResultStatus.COMPLETED
    assert voice.tts_degraded is True
    assert mock_tts.synthesize.call_count == 1

    # Turn 2: Subsequent turn does NOT attempt synthesize again
    text2, res2 = voice.process_voice_input(b"audio2", speak_response=True)
    assert text2 == "how are you"
    assert res2.status == ResultStatus.COMPLETED
    # Still 1 call, not 2
    assert mock_tts.synthesize.call_count == 1


# =====================================================================
# 13 & 14. Failure Recovery & Compound Task Tests
# =====================================================================

def test_failure_recovery_does_not_poison_subsequent_turns():
    """Verify a failed action in Turn 1 does not poison Turn 2 execution."""
    class FailThenSucceedHandler:
        def __init__(self):
            self.calls = 0

        def run(self, task_input, context):
            from tasks.types import TaskOutput
            self.calls += 1
            if task_input.intent == "fail_intent":
                return TaskOutput(content="File not found", success=False, error="FileNotFoundError")
            return TaskOutput(content="Created file successfully", success=True, metadata={"path": "new.txt"})

    handler = FailThenSucceedHandler()
    executor = TaskExecutor(handlers={"fail_intent": handler, "succeed_intent": handler})

    plan_fail = ExecutionPlan(steps=(
        PlanStep(description="read missing", intent="fail_intent", metadata={"replan_on_failure": False}),
    ))
    plan_succeed = ExecutionPlan(steps=(
        PlanStep(description="create file", intent="succeed_intent", metadata={"path": "new.txt"}),
    ))

    class SequentialPlanner:
        def __init__(self):
            self.plans = [plan_fail, plan_succeed]
            self.idx = 0

        def plan(self, context):
            p = self.plans[self.idx]
            self.idx = min(self.idx + 1, len(self.plans) - 1)
            return p

    brain = Brain(planner=SequentialPlanner(), executor=executor)

    # Turn 1: Fails
    res1 = brain.run("read nonexistent.txt")
    assert res1.status == ResultStatus.FAILED
    assert brain._pending_approval is None

    # Turn 2: Next independent request executes cleanly and succeeds
    res2 = brain.run("create new.txt and write Hello inside")
    assert res2.status == ResultStatus.COMPLETED
    assert res2.output == "Created file successfully"
    assert brain._active_entities.get("file") == "new.txt"


def test_compound_task_with_high_risk_step_pauses_and_completes():
    """Verify compound plan with low risk -> high risk pauses, and on approval finishes remaining steps."""
    from tasks.types import TaskOutput

    executed_steps = []
    class CompoundHandler:
        def run(self, task_input, context):
            executed_steps.append(task_input.intent)
            return TaskOutput(content=f"Ran {task_input.intent}", success=True)

    handler = CompoundHandler()
    executor = TaskExecutor(handlers={"step_1": handler, "step_2_high": handler, "step_3": handler})

    step1 = PlanStep(description="low risk step 1", intent="step_1", metadata={})
    step2 = PlanStep(description="destructive step 2", intent="step_2_high", metadata={"risk_level": "high", "destructive": True})
    step3 = PlanStep(description="low risk step 3", intent="step_3", metadata={})

    plan = ExecutionPlan(steps=(step1, step2, step3))

    class StaticPlanner:
        def plan(self, context):
            return plan

    brain = Brain(planner=StaticPlanner(), executor=executor)

    # Initial turn: Step 1 runs, pauses at Step 2
    res1 = brain.run("run compound task")
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in res1.output
    assert executed_steps == ["step_1"]

    # Approval turn: User approves, Step 2 and Step 3 run
    res2 = brain.run("Yes, please proceed.")
    assert res2.status == ResultStatus.COMPLETED
    assert executed_steps == ["step_1", "step_2_high", "step_3"]
    assert brain._pending_approval is None
