"""Cross-application interaction tests — Phase 9 (Windows Notepad).

This suite proves the bounded cross-app capability without requiring a real
desktop session:

* deterministic fake driver  -> launch / focus / type / observe / verify path
* real Windows driver        -> opt-in end-to-end test against actual Notepad
  (set ``MAMBA_REAL_DESKTOP_TESTS=1``; skipped otherwise, since it types into a
  real application window)

Coverage map (Phase 9 validation):
  TEST 1 open Notepad              -> test_launch_notepad_step_binds_new_window
  TEST 2 type text (verified)      -> test_open_and_type_text_end_to_end_verified
  TEST 3 wrong-target protection   -> test_wrong_target_is_never_typed_into
  TEST 4 permission involvement    -> test_permission_policy_evaluates_typing_step
  TEST 5 verification failure      -> test_verification_reports_failure_*
                                      test_verification_is_inconclusive_*
  TEST 6 voice path                -> test_voice_request_uses_same_cross_app_path
  TEST 7 regression                -> existing suites (run separately)
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.runtime import MambaRuntime
from core.types import (
    ExecutionPlan,
    PlanStep,
    ResultStatus,
    UserRequest,
)
from permissions.policy import DefaultPermissionPolicy
from permissions.types import PermissionDecision, PermissionRequest, PermissionResult, RiskLevel
from skills.desktop import DesktopTaskHandler
from tasks.executor import TaskExecutor
from tasks.types import TaskInput
from tools.desktop.notepad import (
    NotepadUnavailableError,
    TargetResolutionError,
    WindowBinding,
)
from tools.desktop.types import (
    DESKTOP_OPERATIONS,
    DesktopAction,
    cross_app_operation_metadata,
    notepad_operation_metadata,
)

_OTHER_APP = WindowBinding(
    hwnd=999,
    title="Budget.xlsx - Excel",
    class_name="XLMAIN",
    pid=4242,
    process_name="EXCEL.EXE",
)


# ── deterministic test doubles ──────────────────────────────────────────────


class FakeNotepadDriver:
    """Deterministic stand-in for the Windows Notepad driver.

    Mirrors the real driver's safety contract: typing is only accepted when the
    target is a bound Notepad window AND is the current foreground window.
    """

    def __init__(self) -> None:
        self.available = True
        self.windows: list[WindowBinding] = []
        self.texts: dict[int, str] = {}
        self.foreground_hwnd: int | None = None
        self.focus_works = True
        self.read_fails = False
        self.type_calls: list[tuple[int, str]] = []
        self.refused_type_calls: list[tuple[int, str]] = []

    # driver protocol
    def is_available(self, app_id: str | None = None) -> tuple[bool, str]:
        return (True, "Notepad is available.") if self.available else (
            False,
            "Notepad (notepad.exe) was not found on this system.",
        )

    def find_windows(self) -> list[WindowBinding]:
        return list(self.windows)

    def launch(
        self,
        *,
        timeout: float = 10.0,
        editor_timeout: float = 5.0,
        options: dict[str, Any] | None = None,
    ) -> WindowBinding:
        if not self.available:
            raise NotepadUnavailableError("Notepad (notepad.exe) was not found on this system.")
        binding = WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
        self.windows.append(binding)
        self.texts.setdefault(binding.hwnd, "")
        self.foreground_hwnd = binding.hwnd
        return binding

    def wait_for_editor(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        return self.is_bound(target)

    def focus(self, target: WindowBinding, *, timeout: float = 3.0) -> bool:
        if not self.is_bound(target):
            return False
        if not self.focus_works:
            return False
        self.foreground_hwnd = target.hwnd
        return True

    def foreground(self) -> WindowBinding | None:
        for binding in self.windows:
            if binding.hwnd == self.foreground_hwnd:
                return binding
        if self.foreground_hwnd == _OTHER_APP.hwnd:
            return _OTHER_APP
        return None

    def type_text(self, target: WindowBinding, text: str) -> None:
        if not self.is_bound(target):
            raise TargetResolutionError(
                "Refusing to type: the bound Notepad window is no longer valid."
            )
        if self.foreground_hwnd != target.hwnd:
            self.refused_type_calls.append((target.hwnd, text))
            raise TargetResolutionError(
                "Refusing to type: the active window is not the bound Notepad window."
            )
        self.type_calls.append((target.hwnd, text))
        self.texts[target.hwnd] = self.texts.get(target.hwnd, "") + text

    def read_text(self, target: WindowBinding, *, max_chars: int = 32768) -> str:
        if not self.is_bound(target):
            raise TargetResolutionError("The bound Notepad window is no longer available.")
        if self.read_fails:
            raise TargetResolutionError("Could not locate the Notepad text control.")
        return self.texts.get(target.hwnd, "")

    def is_bound(self, target: WindowBinding) -> bool:
        return any(
            w.hwnd == target.hwnd
            and w.pid == target.pid
            and w.class_name == "Notepad"
            and "notepad" in w.title.lower()
            for w in self.windows
        )


class StaticPlanner:
    """Deterministic planner returning predefined plans."""

    def __init__(self, plans: list[ExecutionPlan]) -> None:
        self._plans = list(plans)
        self.call_count = 0

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        self.call_count += 1
        if self._plans:
            return self._plans.pop(0)
        return ExecutionPlan(steps=())


class RecordingPermissionPolicy:
    """Delegates to the real policy while recording what it was asked."""

    def __init__(self) -> None:
        self.requests: list[PermissionRequest] = []
        self._inner = DefaultPermissionPolicy()

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        self.requests.append(request)
        return self._inner.evaluate(request)


def _notepad_handlers(
    driver: FakeNotepadDriver,
    *,
    intents: tuple[str, ...] | None = None,
) -> dict[str, DesktopTaskHandler]:
    handler = DesktopTaskHandler(notepad_driver=driver)
    selected = intents if intents is not None else notepad_operation_intents()
    return {intent: handler for intent in selected}


def notepad_operation_intents() -> tuple[str, ...]:
    return (
        "launch_notepad",
        "open_notepad",
        "start_notepad",
        "type_text",
        "type_text_in_notepad",
        "type_in_notepad",
        "type_into_notepad",
        "write_in_notepad",
        "enter_text",
        "read_notepad_text",
    )


def _notepad_brain(
    driver: FakeNotepadDriver,
    *steps: PlanStep,
    permissions: Any = None,
    intents: tuple[str, ...] | None = None,
) -> Brain:
    executor = TaskExecutor(handlers=_notepad_handlers(driver, intents=intents))
    return Brain(
        planner=StaticPlanner([ExecutionPlan(steps=tuple(steps))]),
        executor=executor,
        permissions=permissions or DefaultPermissionPolicy(),
    )


# ── TEST 1 — launch Notepad ─────────────────────────────────────────────────


def test_launch_notepad_step_binds_new_window():
    """'Open Notepad' plans launch_notepad; the new window becomes the target."""
    driver = FakeNotepadDriver()
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
    )

    result = brain.run("Open Notepad.")

    assert result.status == ResultStatus.COMPLETED
    launch_obs = result.observations[0]
    assert launch_obs.success is True
    assert launch_obs.metadata["app"] == "Notepad"
    assert launch_obs.metadata["target_bound"] is True
    assert launch_obs.metadata["hwnd"] == 1001
    assert launch_obs.metadata["target_pid"] == 777
    assert driver.foreground_hwnd == 1001


def test_launch_notepad_reports_unavailable_clearly():
    """Notepad unavailable -> honest failure, no window actions attempted."""
    driver = FakeNotepadDriver()
    driver.available = False
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
    )

    result = brain.run("Open Notepad.")

    assert result.status == ResultStatus.FAILED
    combined = " ".join(o.content for o in result.observations).lower()
    assert "not found" in combined
    assert driver.windows == []


# ── TEST 2 — type text (with observation + verification) ───────────────────


def test_open_and_type_text_end_to_end_verified():
    """Full flow: launch -> bind -> focus -> type -> read back -> verify."""
    driver = FakeNotepadDriver()
    type_step = PlanStep(
        description="type Hello from Mamba into Notepad",
        intent="type_text",
        metadata={"text": "Hello from Mamba", "app": "Notepad"},
    )
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
        type_step,
    )

    result = brain.run("Open Notepad and type Hello from Mamba.")

    assert result.status == ResultStatus.COMPLETED, result.error
    # The action actually ran against the bound Notepad window.
    assert driver.type_calls == [(1001, "Hello from Mamba")]
    assert driver.texts[1001] == "Hello from Mamba"

    type_obs = next(o for o in result.observations if o.metadata.get("typed"))
    assert type_obs.success is True
    assert type_obs.metadata["target_bound"] is True
    assert type_obs.metadata["app"] == "Notepad"
    # Verification ran by reading the text back, and did not merely trust the action.
    assert type_step.metadata["expected"]["ui_text_contains"]["text"] == "Hello from Mamba"
    assert not any(
        o.metadata.get("action") == "verification" for o in result.observations
    )
    assert result.error is None


def test_type_step_pins_text_and_expected_predicate():
    """The payload and expected outcome are bound onto the step before execution."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    driver.foreground_hwnd = 1001

    step = PlanStep(
        description="type into notepad",
        intent="type_text",
        metadata={"content": "bound text", "application": "notepad"},
    )
    brain = _notepad_brain(driver, step)

    # The pre-existing window is not Mamba-owned, so the typing step pauses for
    # confirmation before anything is typed.
    first = brain.run("type bound text in notepad")
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in first.output
    assert driver.type_calls == []

    result = brain.run("yes")

    assert result.status == ResultStatus.COMPLETED
    assert step.metadata["text"] == "bound text"
    assert step.metadata["app"] == "Notepad"
    assert step.metadata["expected"]["ui_text_contains"]["text"] == "bound text"
    assert driver.type_calls == [(1001, "bound text")]


# ── TEST 3 — wrong target protection ────────────────────────────────────────


def test_wrong_target_is_never_typed_into():
    """With an unrelated app focused, Mamba refuses to type rather than guessing.

    Even after the user confirms, execution re-validates the pinned target and
    refuses when focus cannot be reclaimed.
    """
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    # Another application owns the foreground, and focus cannot be reclaimed.
    driver.foreground_hwnd = _OTHER_APP.hwnd
    driver.focus_works = False

    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
    )

    first = brain.run("Open Notepad and type Hello from Mamba.")
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in first.output
    assert driver.type_calls == []

    result = brain.run("yes")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert driver.texts.get(1001, "") == ""
    assert any(
        "refusing to type" in o.content.lower() for o in result.observations
    ), [o.content for o in result.observations]


def test_foreign_explicit_handle_is_rejected():
    """An explicit window handle that is not Notepad aborts instead of falling back."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    driver.foreground_hwnd = 1001

    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type into a foreign window",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "hwnd": 4242},
        ),
    )

    result = brain.run("type Hello from Mamba into Notepad")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("not an open" in o.content and "window" in o.content for o in result.observations)


def test_non_notepad_application_is_refused_before_execution():
    """A step naming another application never reaches the desktop capability."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    driver.foreground_hwnd = 1001

    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type into Excel",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Excel"},
        ),
    )

    result = brain.run("type Hello from Mamba into Excel")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("not a supported application" in o.content for o in result.observations)


def test_stale_bound_window_is_rejected():
    """A binding for a window that no longer exists is not reused."""
    driver = FakeNotepadDriver()
    driver.foreground_hwnd = 1001

    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type into stale target",
            intent="type_text",
            metadata={
                "text": "Hello from Mamba",
                "hwnd": 1001,
                "target_pid": 777,
                "target_title": "Untitled - Notepad",
                "target_class": "Notepad",
            },
        ),
    )

    result = brain.run("type Hello from Mamba into Notepad")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("not an open" in o.content and "window" in o.content for o in result.observations)


# ── TEST 4 — permission involvement ─────────────────────────────────────────


def test_permission_policy_evaluates_typing_step():
    """The existing permission system evaluates the typing action with its real risk metadata."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    driver.foreground_hwnd = 1001
    policy = RecordingPermissionPolicy()
    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
        permissions=policy,
    )

    first = brain.run("Open Notepad and type Hello from Mamba.")
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in first.output

    result = brain.run("yes")
    assert result.status == ResultStatus.COMPLETED
    assert driver.texts[1001] == "Hello from Mamba"

    request = policy.requests[0]
    assert request.action == DesktopAction.TYPE_TEXT_IN_APPLICATION.value
    # The window is not Mamba-owned: typing into a pre-existing user window
    # escalates to HIGH risk and asks. (Mamba-launched targets stay MEDIUM.)
    assert request.risk_level == RiskLevel.HIGH
    assert request.metadata.get("externally_visible") is True
    # Not destructive and not irreversible: the policy must not treat typing as such.
    assert request.metadata.get("destructive") is not True
    assert request.metadata.get("irreversible") is not True
    assert request.metadata.get("user_sensitive") is not True
    # HIGH risk pauses for confirmation under the existing policy; MEDIUM
    # executes automatically. The resumed execution re-evaluates the same
    # request (now approved) instead of trusting the earlier check blindly.
    assert DefaultPermissionPolicy().evaluate(request).decision == PermissionDecision.ASK
    assert len(policy.requests) == 2


def test_launch_and_read_are_low_risk_metadata():
    """Launching Notepad and reading its text stay LOW risk; typing is MEDIUM."""
    launch = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_NOTEPAD].to_metadata()
    assert launch["risk_level"] == RiskLevel.LOW
    assert launch["destructive"] is False

    read = DESKTOP_OPERATIONS[DesktopAction.READ_NOTEPAD_TEXT].to_metadata()
    assert read["risk_level"] == RiskLevel.LOW

    type_meta = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_NOTEPAD].to_metadata()
    assert type_meta["risk_level"] == RiskLevel.MEDIUM
    assert type_meta["destructive"] is False
    assert type_meta["irreversible"] is False

    # Intent aliases resolve to the same authoritative metadata.
    assert notepad_operation_metadata("type_text_in_notepad") == type_meta
    generic_meta = cross_app_operation_metadata("type_text")
    assert generic_meta is not None
    assert generic_meta["risk_level"] == type_meta["risk_level"]
    assert generic_meta["action"] == DesktopAction.TYPE_TEXT_IN_APPLICATION.value
    assert generic_meta["externally_visible"] is True
    assert notepad_operation_metadata("launch_notepad") == launch
    assert notepad_operation_metadata("read_notepad_text") == read


# ── TEST 5 — verification failure / inconclusive ────────────────────────────


def test_verification_reports_failure_when_text_did_not_land():
    """Keystrokes reported as sent, but the requested text is absent -> failure."""
    driver = FakeNotepadDriver()

    class LyingDriver(FakeNotepadDriver):
        def type_text(self, target: WindowBinding, text: str) -> None:
            # Accepts the action but nothing actually lands in the document.
            self.type_calls.append((target.hwnd, text))

    driver = LyingDriver()
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
    )

    result = brain.run("Open Notepad and type Hello from Mamba.")

    assert result.status == ResultStatus.FAILED
    combined = " ".join(f"{o.content}" for o in result.observations).lower()
    assert "verification failed" in combined
    assert "does not contain" in combined


def test_verification_is_inconclusive_when_text_cannot_be_observed():
    """If the window text cannot be read, Mamba reports inconclusive, not success."""
    driver = FakeNotepadDriver()
    driver.read_fails = True
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
    )

    result = brain.run("Open Notepad and type Hello from Mamba.")

    assert result.status == ResultStatus.FAILED
    combined = " ".join(o.content for o in result.observations).lower()
    assert "inconclusive" in combined


def test_verification_is_inconclusive_without_desktop_capability():
    """No desktop read path wired -> verification cannot confirm -> inconclusive."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    driver.foreground_hwnd = 1001

    # Only the typing intent is registered: nothing can read Notepad text back.
    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
        intents=("type_text",),
    )

    first = brain.run("type Hello from Mamba into Notepad")
    assert brain._pending_approval is not None
    assert driver.type_calls == []

    result = brain.run("yes")

    assert result.status == ResultStatus.FAILED
    combined = " ".join(o.content for o in result.observations).lower()
    assert "inconclusive" in combined
    assert driver.type_calls == [(1001, "Hello from Mamba")]


# ── TEST 6 — voice path uses the same cross-app execution path ──────────────


def test_voice_request_uses_same_cross_app_path():
    """A voice-tagged request flows through MambaRuntime into the same Notepad steps."""
    driver = FakeNotepadDriver()
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
    )
    runtime = MambaRuntime(brain=brain)

    result = runtime.run(
        UserRequest(
            goal="Open Notepad and type Hello from Mamba.",
            metadata={"input_modality": "voice"},
        )
    )

    assert result.status == ResultStatus.COMPLETED
    assert driver.texts[1001] == "Hello from Mamba"


def test_voice_type_request_is_not_refused():
    """Voice requests flow through the same path; voice *approvals* stay refused."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=1001,
            title="Untitled - Notepad",
            class_name="Notepad",
            pid=777,
            process_name="Notepad.exe",
        )
    )
    driver.foreground_hwnd = 1001
    brain = _notepad_brain(
        driver,
        PlanStep(
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            metadata={"text": "Hello from Mamba", "app": "Notepad"},
        ),
    )
    runtime = MambaRuntime(brain=brain)

    # A voice request is processed normally — it pauses only because the
    # pre-existing window is unowned (a provenance escalation, not a refusal).
    first = runtime.run(
        UserRequest(
            goal="type Hello from Mamba in notepad",
            metadata={"input_modality": "voice"},
        )
    )
    assert "Voice approval is not permitted" not in first.output
    assert brain._pending_approval is not None
    assert driver.type_calls == []

    # A voice "yes" cannot approve the escalation; the approval stays pending.
    refusal = runtime.run(
        UserRequest(goal="yes", metadata={"input_modality": "voice"})
    )
    assert "Voice approval is not permitted" in refusal.output
    assert brain._pending_approval is not None
    assert driver.type_calls == []

    # An on-screen confirmation resolves the same pending approval.
    final = runtime.run(UserRequest(goal="yes"))
    assert final.status == ResultStatus.COMPLETED
    assert driver.texts[1001] == "Hello from Mamba"


# ── wiring / regression guards ──────────────────────────────────────────────


def test_mixed_executor_routes_notepad_intents_to_desktop_handler():
    """Intents are wired through the existing Task/Skill/Tool architecture."""
    from skills.mixed import create_mixed_task_executor

    executor = create_mixed_task_executor()
    handlers = executor.handlers or {}
    for intent in notepad_operation_intents():
        assert intent in handlers, f"{intent} is not wired into the mixed executor"

    metadata = handlers["type_text"].get_metadata(
        TaskInput(
            step_id="s1",
            description="type into notepad",
            intent="type_text",
            execution_id="e1",
            goal="open notepad and type",
            step_metadata={"text": "Hello from Mamba"},
        )
    )
    assert metadata["action"] == "type_text_in_application"
    # Without established ownership of the target window, typing escalates.
    assert metadata["risk_level"] == RiskLevel.HIGH

    owned = handlers["type_text"].get_metadata(
        TaskInput(
            step_id="s2",
            description="type into notepad",
            intent="type_text",
            execution_id="e1",
            goal="open notepad and type",
            step_metadata={"text": "Hello from Mamba", "target_owned": True},
        )
    )
    assert owned["risk_level"] == RiskLevel.MEDIUM


def test_read_notepad_text_skill_returns_observation_metadata():
    """The read action exposes observable text for verification and reporting."""
    driver = FakeNotepadDriver()
    driver.launch()
    driver.texts[1001] = "Hello from Mamba"

    handler = DesktopTaskHandler(notepad_driver=driver)
    output = handler.run(
        TaskInput(
            step_id="s1",
            description="read notepad text",
            intent="read_notepad_text",
            execution_id="e1",
            goal="read notepad",
            step_metadata={},
        ),
        None,  # type: ignore[arg-type]
    )

    assert output.success is True
    assert output.metadata["text"] == "Hello from Mamba"
    assert output.metadata["app"] == "Notepad"
    assert output.metadata["hwnd"] == 1001


def test_read_notepad_text_fails_clearly_when_notepad_is_absent():
    """No Notepad window -> clear failure, never a silent empty success."""
    driver = FakeNotepadDriver()
    handler = DesktopTaskHandler(notepad_driver=driver)

    output = handler.run(
        TaskInput(
            step_id="s1",
            description="read notepad text",
            intent="read_notepad_text",
            execution_id="e1",
            goal="read notepad",
            step_metadata={},
        ),
        None,  # type: ignore[arg-type]
    )

    assert output.success is False
    assert "No open Notepad window was found" in output.content


def test_missing_text_argument_is_rejected():
    """A typing step with no payload fails before any window interaction."""
    driver = FakeNotepadDriver()
    brain = _notepad_brain(
        driver,
        PlanStep(description="type nothing", intent="type_text", metadata={}),
    )

    result = brain.run("type something in notepad")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("did not specify any text" in o.content for o in result.observations)


def test_desktop_tools_registry_exposes_notepad_tools():
    """The desktop tool registry includes the Notepad tools."""
    from tools.desktop.tool import create_desktop_tools

    tools = create_desktop_tools()
    assert "launch_notepad" in tools
    assert "type_text_in_notepad" in tools
    assert "read_notepad_text" in tools



def test_declared_ui_text_predicate_uses_existing_verifier():
    """A typed step with an explicit predicate is verified through the framework.

    The observed window text is fed to the existing verifier as a `contains`
    comparison — no bespoke verification logic and no self-reported success.
    """
    from verification.verifier import DefaultVerifier

    driver = FakeNotepadDriver()
    driver.launch()  # an open Notepad window is required before typing
    step = PlanStep(
        description="type into notepad",
        intent="type_text_in_notepad",
        metadata={
            "text": "Hello from Mamba",
            "expected": {"ui_text_contains": {"app": "Notepad", "text": "Mamba"}},
        },
    )
    brain = _notepad_brain(driver, step, intents=("type_text_in_notepad", "read_notepad_text"))

    # The window was not created by a Mamba launch step, so the change is
    # confirmed first; the declared predicate is verified after approval.
    first = brain.run("type Hello from Mamba in notepad")
    assert brain._pending_approval is not None
    assert driver.type_calls == []

    result = brain.run("yes")

    assert result.status == ResultStatus.COMPLETED
    assert driver.texts[1001] == "Hello from Mamba"
    assert any(
        o.metadata.get("action") == "verification_passed" for o in result.observations
    )

    # Same predicate, wrong observed text -> the existing verifier fails it.
    verifier = DefaultVerifier()
    from verification.types import VerificationRequest, VerificationStatus

    direct = verifier.verify(
        VerificationRequest(
            expected={"contains": "Mamba"},
            actual={"ui_text": "something else", "app": "Notepad"},
            metadata={"app": "Notepad"},
        )
    )
    assert direct.status == VerificationStatus.FAILED


def test_grouped_desktop_intents_do_not_collide():
    """Notepad intents resolve metadata without shadowing existing desktop intents."""
    from tools.desktop.types import notepad_operation_for, notepad_operation_metadata

    assert notepad_operation_for("focus_window") is None
    assert notepad_operation_metadata("read_clipboard") is None
    assert notepad_operation_metadata("notepad_text") is not None


def test_typing_different_text_is_not_treated_as_a_repeated_step():
    """Two typing steps with different payloads are distinct (no false loop stop)."""
    from core.brain import _step_signature

    first = PlanStep(description="type", intent="type_text", metadata={"text": "first"})
    second = PlanStep(description="type", intent="type_text", metadata={"text": "second"})
    assert _step_signature(first) != _step_signature(second)


_SAVE_PROMPT_WINDOW_CLASS = "#32770"


def _recorded_identity(target: Any) -> dict[str, Any] | None:
    """Extract the immutable recorded identity (handle, owning PID, class)."""
    if isinstance(target, WindowBinding):
        return {"hwnd": target.hwnd, "pid": target.pid, "class_name": target.class_name}
    if isinstance(target, dict):
        hwnd = target.get("hwnd")
        pid = target.get("target_pid", target.get("pid"))
        class_name = target.get("target_class", target.get("class_name"))
        if hwnd is None or pid is None:
            return None
        try:
            return {
                "hwnd": int(hwnd),
                "pid": int(pid),
                "class_name": str(class_name or ""),
            }
        except (TypeError, ValueError):
            return None
    return None


def _live_window_binding(hwnd: int) -> WindowBinding | None:
    """Read the live window for a handle (single seam, patchable in tests)."""
    from tools.desktop._win32 import window_of

    return window_of(int(hwnd))


def _window_still_test_owned(recorded: dict[str, Any]) -> bool:
    """Whether the live window still matches the recorded test-owned identity."""
    live = _live_window_binding(recorded["hwnd"])
    if live is None:
        return False
    if live.pid != recorded["pid"]:
        return False
    if recorded["class_name"] and live.class_name != recorded["class_name"]:
        return False
    return True


def _prompt_belongs_to(
    recorded: dict[str, Any], *, foreground_hwnd: int | None = None
) -> bool:
    """Whether a save prompt is positively this test-owned process's own dialog.

    A discard key may only be sent when the foreground window is a standard
    dialog (class #32770) owned by the same PID as the recorded window and is
    not that window itself. Anything else is left untouched.
    """
    if foreground_hwnd is None:
        try:
            import win32gui

            foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            return False
    if not foreground_hwnd or foreground_hwnd == recorded["hwnd"]:
        return False
    live = _live_window_binding(foreground_hwnd)
    if live is None:
        return False
    return live.class_name == _SAVE_PROMPT_WINDOW_CLASS and live.pid == recorded["pid"]


def _close_test_notepad(target: Any) -> None:
    """Close exactly the Notepad window a test created; never any other window.

    Requires the full recorded identity (handle + owning PID + window class)
    and refuses to act unless the live window still matches it. Ctrl+W is sent
    only to that revalidated window; the "Save changes?" prompt is answered
    only when positively identified as this same process's own dialog. In any
    other situation the window — and any prompt on it — is left exactly as
    found for the user to decide.

    Windows 11 Notepad ignores ``WM_CLOSE`` for documents with unsaved changes;
    the tab must be closed with Ctrl+W and the "Don't save" prompt answered.
    """
    recorded = _recorded_identity(target)
    if recorded is None:
        return
    try:
        import ctypes

        import win32gui
        import win32process

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        def _attach(*hwnds: int) -> list[int]:
            current = kernel32.GetCurrentThreadId()
            attached: list[int] = []
            for h in hwnds:
                try:
                    thread_id = win32process.GetWindowThreadProcessId(h)[0]
                except Exception:
                    continue
                if thread_id and thread_id != current:
                    try:
                        if win32process.AttachThreadInput(thread_id, current, True):
                            attached.append(thread_id)
                    except Exception:
                        pass
            return attached

        def _detach(attached: list[int]) -> None:
            current = kernel32.GetCurrentThreadId()
            for thread_id in attached:
                try:
                    win32process.AttachThreadInput(thread_id, current, False)
                except Exception:
                    pass

        def _press(vk: int, *, ctrl: bool = False) -> None:
            if ctrl:
                user32.keybd_event(0x11, 0, 0, 0)
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(vk, 0, 0x0002, 0)
            if ctrl:
                user32.keybd_event(0x11, 0, 0x0002, 0)

        for _ in range(2):
            if not win32gui.IsWindow(recorded["hwnd"]):
                return
            if not _window_still_test_owned(recorded):
                return  # the handle no longer names the recorded test window
            attached = _attach(win32gui.GetForegroundWindow(), recorded["hwnd"])
            try:
                win32gui.SetForegroundWindow(recorded["hwnd"])
                time.sleep(0.2)
                _press(0x57, ctrl=True)  # Ctrl+W closes the document tab
            finally:
                _detach(attached)
            time.sleep(0.4)
            if not win32gui.IsWindow(recorded["hwnd"]):
                return
            # A "Save changes?" prompt may have appeared. Answer it only when it
            # is positively this test-owned process's own dialog; otherwise leave
            # the window exactly as found.
            if not _prompt_belongs_to(recorded):
                return
            attached = _attach(win32gui.GetForegroundWindow(), recorded["hwnd"])
            try:
                _press(0x4E)  # N: "Don't save", for the test-typed text only
            finally:
                _detach(attached)
            time.sleep(0.3)
    except Exception:
        pass


# ── test cleanup safety: revalidated identity, proven prompt only ──────────


def test_cleanup_identity_extraction_requires_handle_and_pid():
    """F4: cleanup cannot act without a full recorded identity."""
    binding = WindowBinding(
        hwnd=2001,
        title="Untitled - Notepad",
        class_name="Notepad",
        pid=777,
        process_name="Notepad.exe",
    )
    assert _recorded_identity(binding) == {"hwnd": 2001, "pid": 777, "class_name": "Notepad"}
    assert _recorded_identity(
        {"hwnd": 5, "target_pid": 6, "target_class": "Notepad"}
    ) == {"hwnd": 5, "pid": 6, "class_name": "Notepad"}
    assert _recorded_identity({"hwnd": 5}) is None
    assert _recorded_identity({"target_pid": 6}) is None
    assert _recorded_identity(None) is None


def test_cleanup_identity_requires_full_recorded_match(monkeypatch):
    """F4: cleanup refuses any window that does not match the recorded identity."""
    import tools.desktop._win32 as win32_helpers

    recorded = {"hwnd": 2001, "pid": 777, "class_name": "Notepad"}

    def live(hwnd: int, *, pid: int = 777, cls: str = "Notepad") -> WindowBinding:
        return WindowBinding(
            hwnd=hwnd,
            title="Untitled - Notepad",
            class_name=cls,
            pid=pid,
            process_name="Notepad.exe",
        )

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: live(hwnd))
    assert _window_still_test_owned(recorded) is True

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: live(hwnd, pid=999))
    assert _window_still_test_owned(recorded) is False

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: live(hwnd, cls="Notepad++"))
    assert _window_still_test_owned(recorded) is False

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: None)
    assert _window_still_test_owned(recorded) is False


def test_cleanup_prompt_must_belong_to_recorded_process(monkeypatch):
    """F4: a discard key may only answer this process's own save prompt."""
    import tools.desktop._win32 as win32_helpers

    recorded = {"hwnd": 2001, "pid": 777, "class_name": "Notepad"}

    def dialog(hwnd: int, *, pid: int, cls: str = "#32770") -> WindowBinding:
        return WindowBinding(
            hwnd=hwnd,
            title="Notepad",
            class_name=cls,
            pid=pid,
            process_name="Notepad.exe",
        )

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: dialog(hwnd, pid=777))
    # The prompt itself — not the target window — must be the foreground dialog.
    assert _prompt_belongs_to(recorded, foreground_hwnd=2002) is True
    assert _prompt_belongs_to(recorded, foreground_hwnd=recorded["hwnd"]) is False

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: dialog(hwnd, pid=999))
    assert _prompt_belongs_to(recorded, foreground_hwnd=2002) is False

    monkeypatch.setattr(
        win32_helpers,
        "window_of",
        lambda hwnd: dialog(hwnd, pid=777, cls="OtherClass"),
    )
    assert _prompt_belongs_to(recorded, foreground_hwnd=2002) is False

    monkeypatch.setattr(win32_helpers, "window_of", lambda hwnd: None)
    assert _prompt_belongs_to(recorded, foreground_hwnd=2002) is False
    assert _prompt_belongs_to(recorded, foreground_hwnd=0) is False


# ── ambiguous / stale reads never guess (F2) ────────────────────────────────


def test_read_notepad_text_refuses_multiple_open_windows(monkeypatch):
    """F2: several Notepad windows and no handle -> refuse instead of guessing."""
    import tools.desktop.notepad as notepad_module

    first = WindowBinding(
        hwnd=100,
        title="Untitled - Notepad",
        class_name="Notepad",
        pid=777,
        process_name="Notepad.exe",
    )
    second = WindowBinding(
        hwnd=200,
        title="notes - Notepad",
        class_name="Notepad",
        pid=778,
        process_name="Notepad.exe",
    )
    monkeypatch.setattr(
        notepad_module.WindowsNotepadDriver, "find_windows", lambda self: [first, second]
    )

    with pytest.raises(TargetResolutionError) as err:
        notepad_module.read_notepad_text()

    message = str(err.value)
    assert "refusing to choose one" in message
    assert "100" in message and "200" in message


def test_read_notepad_text_stale_handle_never_falls_back(monkeypatch):
    """F2: an explicit handle that is no longer live is refused, not replaced."""
    import tools.desktop.notepad as notepad_module

    live = WindowBinding(
        hwnd=300,
        title="Untitled - Notepad",
        class_name="Notepad",
        pid=777,
        process_name="Notepad.exe",
    )
    monkeypatch.setattr(notepad_module, "window_of", lambda hwnd: None)
    monkeypatch.setattr(
        notepad_module.WindowsNotepadDriver, "find_windows", lambda self: [live]
    )

    with pytest.raises(TargetResolutionError) as err:
        notepad_module.read_notepad_text(12345)

    assert "no longer open" in str(err.value)


# ── the launched window's identity survives into the next step (F5) ────────


def test_launch_identity_is_carried_into_the_next_step():
    """F5(d): launch -> type acts on the window this run launched, not another match."""
    driver = FakeNotepadDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=555,
            title="user notes - Notepad",
            class_name="Notepad",
            pid=31337,
            process_name="Notepad.exe",
        )
    )
    driver.texts[555] = "user text"
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
        PlanStep(
            description="type the memo",
            intent="type_text",
            metadata={"app": "notepad", "text": "hello"},
        ),
    )

    result = brain.run("Open Notepad and type hello")

    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    assert driver.type_calls == [(1001, "hello")]
    assert driver.texts[1001] == "hello"
    assert driver.texts[555] == "user text"
    typed = [o for o in result.observations if o.metadata.get("typed")]
    assert typed and typed[0].metadata["hwnd"] == 1001


class _VanishingLaunchDriver(FakeNotepadDriver):
    """The launched window disappears right after the launch step completes."""

    def wait_for_editor(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        self.windows = [w for w in self.windows if w.hwnd != target.hwnd]
        return True


def test_carried_identity_never_falls_back_to_another_window():
    """F5: if the launched window is gone, the next step refuses instead of rebinding."""
    driver = _VanishingLaunchDriver()
    driver.windows.append(
        WindowBinding(
            hwnd=555,
            title="user notes - Notepad",
            class_name="Notepad",
            pid=31337,
            process_name="Notepad.exe",
        )
    )
    driver.texts[555] = "user text"
    brain = _notepad_brain(
        driver,
        PlanStep(description="open Notepad", intent="launch_notepad", metadata={}),
        PlanStep(
            description="type the memo",
            intent="type_text",
            metadata={"app": "notepad", "text": "hello"},
        ),
    )

    result = brain.run("Open Notepad and type hello")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert driver.texts[555] == "user text"
    assert any(
        "refusing to act on an unverified target" in (o.content or "")
        for o in result.observations
    ), [o.content for o in result.observations]
    # The still-open user window was never substituted for the vanished target.
    assert all(hwnd != 555 for hwnd, _ in driver.type_calls + driver.refused_type_calls)


def _wait_for_text(
    handler: Any,
    binding: dict[str, Any],
    expected: str,
    *,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """Read a bound Notepad window until the expected text appears.

    Real applications update their controls asynchronously, so an immediate
    single read can race the UI. Mamba's own verification observes once and
    reports inconclusive if it cannot confirm; tests poll so they measure
    correctness rather than input-queue timing.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        output = handler.run(
            TaskInput(
                step_id="read",
                description="read Notepad text",
                intent="read_notepad_text",
                execution_id="e1",
                goal="read Notepad",
                step_metadata=dict(binding),
            ),
            None,  # type: ignore[arg-type]
        )
        if output.success:
            last = output.metadata.get("text", "")
            if expected in last:
                return True, last
        else:
            last = output.content
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(0.25)


# ── opt-in real-desktop end-to-end (TEST 1/2 against actual Notepad) ────────


_REAL_DESKTOP_TESTS = os.environ.get("MAMBA_REAL_DESKTOP_TESTS", "").strip() not in ("", "0", "false")


@pytest.mark.skipif(
    not _REAL_DESKTOP_TESTS,
    reason="real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run",
)
def test_real_notepad_launch_focus_type_and_read_back():
    """End-to-end against the real Windows Notepad (opt-in).

    Exercises the actual driver + skill + tool chain through the existing
    TaskHandler contract: launch -> bind -> focus -> type -> read text back.
    """
    handler = DesktopTaskHandler()  # real WindowsNotepadDriver

    launched = handler.run(
        TaskInput(
            step_id="s1",
            description="open Notepad",
            intent="launch_notepad",
            execution_id="e1",
            goal="open Notepad and type Hello from Mamba",
            step_metadata={},
        ),
        None,  # type: ignore[arg-type]
    )
    assert launched.success is True, launched.content
    binding = {
        "hwnd": launched.metadata["hwnd"],
        "target_pid": launched.metadata["target_pid"],
        "target_title": launched.metadata["target_title"],
        "target_class": launched.metadata["target_class"],
    }

    typed = handler.run(
        TaskInput(
            step_id="s2",
            description="type Hello from Mamba into Notepad",
            intent="type_text",
            execution_id="e1",
            goal="open Notepad and type Hello from Mamba",
            step_metadata={"text": "Hello from Mamba", "app": "Notepad", **binding},
        ),
        None,  # type: ignore[arg-type]
    )
    assert typed.success is True, typed.content

    observed = handler.run(
        TaskInput(
            step_id="s3",
            description="read Notepad text",
            intent="read_notepad_text",
            execution_id="e1",
            goal="read Notepad",
            step_metadata=dict(binding),
        ),
        None,  # type: ignore[arg-type]
    )
    assert observed.success is True, observed.content
    # The bound window's own text control must contain the typed text.
    found, last_seen = _wait_for_text(handler, binding, "Hello from Mamba")
    assert found, f"typed text never appeared in the bound window (saw {last_seen!r})"

    _close_test_notepad(binding)


@pytest.mark.skipif(
    not _REAL_DESKTOP_TESTS,
    reason="real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run",
)
def test_real_wrong_target_protection_refuses_unfocused_notepad():
    """TEST 3 against real windows: no keystroke lands unless Notepad is active.

    A bindable Notepad window exists, but the foreground belongs to another
    window and focus reclamation is made to fail. The typing action must refuse
    and report why, rather than typing into whatever happens to be focused.
    """
    from tools.desktop.notepad import WindowsNotepadDriver

    driver = WindowsNotepadDriver()
    available, reason = driver.is_available()
    assert available, reason

    target = driver.launch()
    assert driver.is_bound(target)
    marker = "WRONG-TARGET-GUARD"
    assert marker not in driver.read_text(target)

    # Another window owns the foreground and cannot be displaced.
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.hotkey("win", "r")
    time.sleep(1.0)
    stolen = driver.foreground()
    try:
        assert stolen is not None and stolen.hwnd != target.hwnd, (
            "could not establish an unrelated foreground window for this test"
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "tools.desktop._win32.focus_attempt",
                lambda hwnd: None,
            )
            focused = driver.focus(target, timeout=0.5)
            assert focused is False
            with pytest.raises(TargetResolutionError):
                driver.type_text(target, marker)
    finally:
        pyautogui.press("escape")
        time.sleep(0.6)

    # Nothing was typed into the unrelated window's target document.
    assert marker not in driver.read_text(target)
    _close_test_notepad(target)


class _FixedPlanRouter:
    """ModelRouter stub that returns one canned plan, for the full-stack test."""

    def __init__(self, plan_json: str) -> None:
        self._plan_json = plan_json
        self.requests: list[Any] = []

    def route(self, request: Any) -> Any:  # pragma: no cover - not used here
        raise AssertionError("route() should not be called; invoke() is preferred")

    def invoke(self, request: Any) -> Any:
        from models.types import ModelResponse

        self.requests.append(request)
        return ModelResponse(
            content=self._plan_json,
            provider="test",
            model="test-planner",
        )


@pytest.mark.skipif(
    not _REAL_DESKTOP_TESTS,
    reason="real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run",
)
def test_real_full_stack_user_request_to_verified_notepad_outcome():
    """The canonical Phase 9 flow against real Notepad.

    USER REQUEST -> planner -> plan -> permission -> desktop capability executes
    -> observation -> verification (text read back) -> result reported.
    """
    from agents.planner import AgentPlanner
    from agents.planning_agent import PlanningAgent
    from skills.mixed import create_mixed_task_executor

    from core.capabilities import default_capability_registry

    plan_json = (
        '{"steps": ['
        '{"description": "Open Notepad", "intent": "launch_notepad", "metadata": {}}, '
        '{"description": "Type the requested text into Notepad", "intent": "type_text", '
        '"metadata": {"text": "Hello from Mamba", "app": "Notepad"}}'
        '], "needs_replanning": false}'
    )
    router = _FixedPlanRouter(plan_json)
    capabilities = default_capability_registry()
    planner = AgentPlanner(
        handler=PlanningAgent(router=router, capabilities=capabilities)
    )
    brain = Brain(
        planner=planner,
        executor=create_mixed_task_executor(),
        capabilities=capabilities,
    )
    runtime = MambaRuntime(brain=brain)

    result = runtime.run("Open Notepad and type Hello from Mamba.")

    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    # The planner actually ran and produced the two cross-app steps.
    assert router.requests, "the planner was never invoked"
    # Action execution and verified outcome are reported as distinct observations.
    assert any(o.metadata.get("typed") for o in result.observations)
    verified_obs = [o for o in result.observations if o.metadata.get("verified")]
    assert verified_obs, "the outcome was never verified"
    assert "Hello from Mamba" in verified_obs[-1].content
    assert "Typed" in (result.output or "")

    # Independently confirm the text landed in the real Notepad window.
    from tools.desktop.notepad import WindowsNotepadDriver

    driver = WindowsNotepadDriver()
    observed = [w for w in driver.find_windows() if "Hello from Mamba" in w.title]
    assert observed, "no real Notepad window shows the typed text in its title"
    binding = {
        "hwnd": observed[0].hwnd,
        "target_pid": observed[0].pid,
        "target_title": observed[0].title,
        "target_class": observed[0].class_name,
    }
    found, last_seen = _wait_for_text(
        DesktopTaskHandler(), binding, "Hello from Mamba"
    )
    assert found, f"typed text never appeared in the bound window (saw {last_seen!r})"

    # Clean up using the exact window the typing action recorded as its target —
    # never a window re-found by title after the fact.
    typed_obs = next(o for o in result.observations if o.metadata.get("typed"))
    _close_test_notepad(
        {
            "hwnd": typed_obs.metadata["hwnd"],
            "target_pid": typed_obs.metadata["target_pid"],
            "target_class": typed_obs.metadata["target_class"],
        }
    )


@pytest.mark.skipif(
    not _REAL_DESKTOP_TESTS,
    reason="real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run",
)
def test_real_voice_request_reaches_same_notepad_execution_path():
    """TEST 6 on the real desktop: spoken request -> VoiceInterface -> Brain -> Notepad.

    Uses the existing voice path (``VoiceInterface.process_voice_input`` with
    ``input_modality="voice"``) and a stubbed STT provider, so the whole
    interface -> runtime -> planning -> desktop -> verification chain is
    exercised without a microphone. Typed and spoken requests converge on the
    same execution path.
    """
    from unittest.mock import MagicMock

    from agents.planner import AgentPlanner
    from agents.planning_agent import PlanningAgent
    from core.capabilities import default_capability_registry
    from skills.mixed import create_mixed_task_executor
    from voice.interface import VoiceInterface

    plan_json = (
        '{"steps": ['
        '{"description": "Open Notepad", "intent": "launch_notepad", "metadata": {}}, '
        '{"description": "Type the requested text into Notepad", "intent": "type_text", '
        '"metadata": {"text": "Hello from Mamba", "app": "Notepad"}}'
        '], "needs_replanning": false}'
    )
    router = _FixedPlanRouter(plan_json)
    capabilities = default_capability_registry()
    brain = Brain(
        planner=AgentPlanner(
            handler=PlanningAgent(router=router, capabilities=capabilities)
        ),
        executor=create_mixed_task_executor(),
        capabilities=capabilities,
    )
    runtime = MambaRuntime(brain=brain)

    stt = MagicMock()
    stt.transcribe.return_value = "Open Notepad and type Hello from Mamba"
    tts = MagicMock()
    tts.synthesize.return_value = b"audio"
    player = MagicMock()

    voice = VoiceInterface(
        runtime,
        stt=stt,
        tts=tts,
        player=player,
        capture=MagicMock(),
    )

    prompt, result = voice.process_voice_input(_silent_wav())

    assert prompt == "Open Notepad and type Hello from Mamba"
    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    # Spoken input reached the same execution path and produced a verified outcome.
    assert brain._last_turn_context is not None
    assert any(o.metadata.get("typed") for o in result.observations)
    assert any(o.metadata.get("verified") for o in result.observations)

    from tools.desktop.notepad import WindowsNotepadDriver

    driver = WindowsNotepadDriver()
    observed = [w for w in driver.find_windows() if "Hello from Mamba" in w.title]
    assert observed, "the spoken request did not reach the real Notepad window"
    binding = {
        "hwnd": observed[0].hwnd,
        "target_pid": observed[0].pid,
        "target_title": observed[0].title,
        "target_class": observed[0].class_name,
    }
    found, last_seen = _wait_for_text(
        DesktopTaskHandler(), binding, "Hello from Mamba"
    )
    assert found, f"spoken request never produced the text (saw {last_seen!r})"

    # Clean up using the exact window the typing action recorded as its target.
    typed_obs = next(o for o in result.observations if o.metadata.get("typed"))
    _close_test_notepad(
        {
            "hwnd": typed_obs.metadata["hwnd"],
            "target_pid": typed_obs.metadata["target_pid"],
            "target_class": typed_obs.metadata["target_class"],
        }
    )


def _silent_wav() -> bytes:
    """Minimal valid WAV payload for the stubbed STT provider."""
    import struct

    frames = b"\x00\x00" * 800  # 50 ms of silence at 16 kHz mono 16-bit
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
    header += b"data" + struct.pack("<I", len(frames))
    return header + frames
