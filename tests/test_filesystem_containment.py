"""Focused filesystem containment tests (CHUNK 1, area D).

Proves that filesystem mutations reachable through the mixed executor are
confined to the authoritative workspace root:

* in-scope operations keep working
* traversal, absolute-path, and symlink escapes are denied before any mutation
* destructive operations (delete) never escape the root
* the write classification resolves the path against the root *before* it
  stats the target (no classification of raw, uncontained paths)
* terminal working directories are contained once a root is wired

Everything runs against isolated ``tmp_path`` resources owned by the test;
nothing outside those temporary directories is ever read or mutated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus, UserRequest
from permissions.policy import DefaultPermissionPolicy
from skills.filesystem import WriteFileSkill
from skills.mixed import create_mixed_task_executor
from tasks.types import TaskInput
from tools.filesystem.types import FILESYSTEM_OPERATIONS, FilesystemAction

_ESCAPE_MESSAGE = "escapes configured root"


class _StaticPlanner:
    """Deterministic planner returning one predefined plan."""

    def __init__(self, steps: tuple[PlanStep, ...]) -> None:
        self._steps = steps

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        steps, self._steps = self._steps, ()
        return ExecutionPlan(steps=steps)


def _task_input(metadata: dict) -> TaskInput:
    return TaskInput(
        step_id="step-1",
        description="write a file",
        intent="write_file",
        execution_id="exec-1",
        goal="write a file",
        step_metadata=metadata,
    )


def _context(goal: str) -> ExecutionContext:
    return ExecutionContext.from_request(UserRequest(goal=goal))


def _write_step(path: str) -> PlanStep:
    return PlanStep(
        description=f"write file {path}",
        intent="write_file",
        metadata={"path": path, "content": "containment check"},
    )


# ── workspace root wiring ───────────────────────────────────────────────────


def test_workspace_root_defaults_to_cwd_and_honors_env(tmp_path: Path, monkeypatch):
    """The authoritative root is the cwd by default, overridable by env."""
    from app import _workspace_root

    monkeypatch.delenv("MAMBA_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _workspace_root() == tmp_path.resolve()

    custom = tmp_path / "custom-root"
    custom.mkdir()
    monkeypatch.setenv("MAMBA_WORKSPACE_ROOT", str(custom))
    assert _workspace_root() == custom.resolve()


# ── in-scope behavior is preserved ──────────────────────────────────────────


def test_in_scope_write_succeeds_through_wired_executor(tmp_path: Path):
    """A relative path inside the root still writes normally."""
    root = tmp_path / "workspace"
    root.mkdir()
    executor = create_mixed_task_executor(root_dir=root)

    observation = executor.execute(_write_step("notes.txt"), _context("write notes.txt"))

    assert observation.success is True
    assert (root / "notes.txt").read_text(encoding="utf-8") == "containment check"


# ── escapes are denied ──────────────────────────────────────────────────────


def test_traversal_write_is_denied(tmp_path: Path):
    """A '../' path out of the root is refused before any write happens."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    executor = create_mixed_task_executor(root_dir=root)

    observation = executor.execute(
        _write_step("../outside/evil.txt"), _context("write a file")
    )

    assert observation.success is False
    assert _ESCAPE_MESSAGE in observation.content
    assert not (outside / "evil.txt").exists()


def test_absolute_outside_write_is_denied(tmp_path: Path):
    """An absolute path outside the root is refused, not silently rewritten."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "evil.txt"
    executor = create_mixed_task_executor(root_dir=root)

    observation = executor.execute(_write_step(str(target)), _context("write a file"))

    assert observation.success is False
    assert _ESCAPE_MESSAGE in observation.content
    assert not target.exists()


def test_symlink_directory_escape_is_denied(tmp_path: Path):
    """A symlinked directory inside the root cannot be used to escape it."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this system")
    executor = create_mixed_task_executor(root_dir=root)

    observation = executor.execute(_write_step("link/evil.txt"), _context("write a file"))

    assert observation.success is False
    assert _ESCAPE_MESSAGE in observation.content
    assert not (outside / "evil.txt").exists()


def test_delete_escape_is_denied_and_leaves_the_target(tmp_path: Path):
    """A destructive delete aimed outside the root is refused; the file stays."""
    root = tmp_path / "workspace"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    executor = create_mixed_task_executor(root_dir=root)
    step = PlanStep(
        description="delete victim.txt",
        intent="delete",
        metadata={"path": str(victim)},
    )

    observation = executor.execute(step, _context("delete victim.txt"))

    assert observation.success is False
    assert _ESCAPE_MESSAGE in observation.content
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "keep me"


# ── resolve before classification ───────────────────────────────────────────


def test_write_classification_resolves_against_root_before_stat(
    tmp_path: Path, monkeypatch
):
    """Classification stats the *resolved* path, never a raw uncontained one."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours", encoding="utf-8")

    skill = WriteFileSkill(root_dir=root)
    # From inside the root, a raw reading of '../outside.txt' would find the
    # non-empty file above and misclassify; resolving against the root first
    # refuses it and falls back to the base (non-destructive) metadata.
    monkeypatch.chdir(root)

    escaping = skill.get_metadata(
        _task_input({"path": "../outside.txt", "content": "x"})
    )
    assert escaping == FILESYSTEM_OPERATIONS[FilesystemAction.WRITE_FILE].to_metadata()
    assert escaping["risk_level"] == "medium"
    assert escaping["destructive"] is False

    # The same rule keeps in-root destructive detection intact.
    inside = root / "existing.txt"
    inside.write_text("already here", encoding="utf-8")
    in_root = skill.get_metadata(
        _task_input({"path": "existing.txt", "content": "x"})
    )
    assert in_root["risk_level"] == "high"
    assert in_root["destructive"] is True


# ── terminal containment through the wired root ─────────────────────────────


def test_terminal_cwd_escape_is_denied_through_wired_executor(tmp_path: Path):
    """A terminal step asking for a cwd outside the root is refused."""
    root = tmp_path / "workspace"
    root.mkdir()
    executor = create_mixed_task_executor(root_dir=root)
    step = PlanStep(
        description="run a command outside the workspace",
        intent="run_command",
        metadata={"command": "echo hello", "cwd": ".."},
    )

    observation = executor.execute(step, _context("run echo hello"))

    assert observation.success is False
    assert _ESCAPE_MESSAGE in observation.content


# ── end-to-end through Brain ────────────────────────────────────────────────


def test_brain_denies_write_escaping_root(tmp_path: Path):
    """Through the full Brain loop an escaping write fails cleanly, writing nothing."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    executor = create_mixed_task_executor(root_dir=root)
    brain = Brain(
        planner=_StaticPlanner((_write_step("../outside/evil.txt"),)),
        executor=executor,
        permissions=DefaultPermissionPolicy(),
    )

    result = brain.run("write a file outside the workspace")

    assert result.status == ResultStatus.FAILED
    assert not (outside / "evil.txt").exists()
    assert any(_ESCAPE_MESSAGE in o.content for o in result.observations)
