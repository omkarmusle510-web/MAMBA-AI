"""Close-window safety tests (F3).

The production ``close_window`` action may only ever act on the exact window
that was recorded when it was bound: an explicit handle plus the owning process
identity, revalidated against the live window immediately before WM_CLOSE.
These tests prove that title-search closes, bare handles without a recorded
identity, stale handles, and replaced windows are refused before any message
is posted — and that another window with a matching identity is never
substituted.
"""

from __future__ import annotations

import pytest

from core.brain import Brain
from core.types import ExecutionPlan, PlanStep, ResultStatus
from permissions.policy import DefaultPermissionPolicy
from skills.desktop import DesktopTaskHandler
from tasks.executor import TaskExecutor
from tools.desktop._win32 import WindowBinding
from tools.desktop.window import CloseWindowHandler
from tools.types import ToolInput


def _live(
    hwnd: int,
    *,
    pid: int = 777,
    cls: str = "Notepad",
    process: str = "Notepad.exe",
) -> WindowBinding:
    return WindowBinding(
        hwnd=hwnd,
        title="Untitled - Notepad",
        class_name=cls,
        pid=pid,
        process_name=process,
    )


@pytest.fixture()
def posted(monkeypatch) -> list[tuple[int, int]]:
    """Record PostMessage calls; report every window as closed afterwards."""
    import win32gui

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        win32gui,
        "PostMessage",
        lambda hwnd, message, wparam, lparam: calls.append((hwnd, message)),
    )
    monkeypatch.setattr(win32gui, "IsWindow", lambda hwnd: False)
    return calls


def test_close_by_title_search_is_refused(posted):
    """A close by title search is refused: it could close the wrong window."""
    out = CloseWindowHandler().run(ToolInput(arguments={"query": "Notepad"}))
    assert out.success is False
    assert "title search" in out.error
    assert posted == []


def test_close_by_recorded_title_field_is_refused(posted):
    """A recorded title alone is not a target identity and is refused."""
    out = CloseWindowHandler().run(ToolInput(metadata={"title": "Untitled - Notepad"}))
    assert out.success is False
    assert "title search" in out.error
    assert posted == []


def test_close_without_recorded_process_identity_is_refused(posted):
    """A bare handle cannot be trusted: the owning process id must be recorded."""
    out = CloseWindowHandler().run(ToolInput(arguments={"hwnd": 5001}))
    assert out.success is False
    assert "owning process identity" in out.error
    assert posted == []


def test_close_stale_handle_is_refused_before_acting(posted, monkeypatch):
    """F3(c): a handle that no longer names a live window fails before any action."""
    monkeypatch.setattr("tools.desktop.window.window_of", lambda hwnd: None)
    out = CloseWindowHandler().run(
        ToolInput(metadata={"hwnd": 5001, "target_pid": 777, "target_class": "Notepad"})
    )
    assert out.success is False
    assert "no longer open" in out.error
    assert posted == []


def test_close_replaced_window_is_refused_before_acting(posted, monkeypatch):
    """F3(c): a handle that now belongs to a different process is refused."""
    monkeypatch.setattr("tools.desktop.window.window_of", lambda hwnd: _live(hwnd, pid=999))
    out = CloseWindowHandler().run(
        ToolInput(metadata={"hwnd": 5001, "target_pid": 777, "target_class": "Notepad"})
    )
    assert out.success is False
    assert "different process" in out.error
    assert posted == []


def test_close_window_with_changed_class_is_refused(posted, monkeypatch):
    """A live window whose class changed is no longer the recorded window."""
    monkeypatch.setattr(
        "tools.desktop.window.window_of", lambda hwnd: _live(hwnd, cls="NotepadNext")
    )
    out = CloseWindowHandler().run(
        ToolInput(metadata={"hwnd": 5001, "target_pid": 777, "target_class": "Notepad"})
    )
    assert out.success is False
    assert "window class changed" in out.error
    assert posted == []


def test_close_posts_wm_close_only_to_the_exact_recorded_window(posted, monkeypatch):
    """F3(e): another live window with the same identity is never substituted."""
    import win32con

    other = _live(6002)  # a second, identically-titled Notepad window
    monkeypatch.setattr(
        "tools.desktop.window.window_of",
        lambda hwnd: _live(hwnd) if hwnd == 5001 else other,
    )
    out = CloseWindowHandler().run(
        ToolInput(metadata={"hwnd": 5001, "target_pid": 777, "target_class": "Notepad"})
    )
    assert out.success is True
    assert out.metadata["close_requested"] is True
    assert posted == [(5001, win32con.WM_CLOSE)]
    assert all(hwnd != other.hwnd for hwnd, _ in posted)


# ── launch -> close identity carry (F5) ────────────────────────────────────


class _FakeLaunchDriver:
    """Minimal Notepad driver double: launching binds one recorded window."""

    def __init__(self) -> None:
        self.windows: list[WindowBinding] = []
        self.foreground_hwnd: int | None = None

    def is_available(self, app_id: str | None = None) -> tuple[bool, str]:
        return True, "Notepad is available."

    def launch(self, *, timeout: float = 10.0, editor_timeout: float = 5.0, options=None):
        binding = WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
        self.windows.append(binding)
        self.foreground_hwnd = binding.hwnd
        return binding

    def wait_for_editor(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        return True

    def focus(self, target: WindowBinding, *, timeout: float = 2.0) -> bool:
        self.foreground_hwnd = target.hwnd
        return True


class _StaticPlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan

    def plan(self, context) -> ExecutionPlan:
        return self._plan


def test_close_step_targets_only_the_window_launched_this_run(posted, monkeypatch):
    """F5(e): launch -> close closes the launched window, never another match."""
    import win32con

    driver = _FakeLaunchDriver()
    driver.windows.append(_live(555, pid=31337))  # pre-existing user window

    handler = DesktopTaskHandler(notepad_driver=driver)
    launch_step = PlanStep(description="open Notepad", intent="launch_notepad", metadata={})
    close_step = PlanStep(description="close Notepad", intent="close_window", metadata={})
    brain = Brain(
        planner=_StaticPlanner(ExecutionPlan(steps=(launch_step, close_step))),
        executor=TaskExecutor(handlers={"launch_notepad": handler, "close_window": handler}),
        permissions=DefaultPermissionPolicy(),
    )

    monkeypatch.setattr(
        "tools.desktop.window.window_of",
        lambda hwnd: _live(hwnd, pid=777 if hwnd == 1001 else 31337)
        if hwnd in (1001, 555)
        else None,
    )

    first = brain.run("Open Notepad and close it")

    # The close is HIGH risk: it must pause for the user, and nothing may be posted yet.
    assert brain._pending_approval is not None
    assert posted == []

    result = brain.run("yes")

    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    assert posted == [(1001, win32con.WM_CLOSE)]
    assert all(hwnd != 555 for hwnd, _ in posted)
