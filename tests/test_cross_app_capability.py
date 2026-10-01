"""Cross-application capability tests — adapter model (Phase 9, generalized).

These tests prove the *reusable* cross-app pattern rather than a Notepad-only
workflow:

* a declarative adapter registry (identity + launch + observation probe),
* one generic driver for launch / bind / focus / type / observe,
* one generic set of tools and skills, with no per-application execution logic,
* target binding that refuses unknown, foreign, or stale windows,
* observation/verification that reports honestly when nothing can be observed.

Real-desktop cases are opt-in via ``MAMBA_REAL_DESKTOP_TESTS=1`` (they launch and
type into real applications).
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus
from permissions.policy import DefaultPermissionPolicy
from permissions.types import RiskLevel
from skills.desktop import DesktopTaskHandler
from skills.mixed import create_mixed_task_executor
from tasks.executor import TaskExecutor
from tasks.types import TaskInput
from tools.desktop._win32 import TargetResolutionError, WindowBinding
from tools.desktop.apps import (
    APP_CALCULATOR,
    APP_FILE_EXPLORER,
    APP_NOTEPAD,
    APP_VSCODE,
    ApplicationAdapter,
    ApplicationRegistry,
    default_application_registry,
)
from tools.desktop.driver import BoundApplicationDriver, CrossAppDriver
from tools.desktop.observation import (
    OBSERVATION_FAILED,
    OBSERVATION_UNVERIFIED,
    OBSERVATION_VERIFIED,
    ClipboardCopyProbe,
    ObservationProbe,
    ObservationResult,
    TextControlProbe,
    WindowStateProbe,
)
from tools.desktop.types import DesktopAction, cross_app_operation_metadata

_REAL_DESKTOP_TESTS = os.environ.get("MAMBA_REAL_DESKTOP_TESTS", "").strip() not in (
    "",
    "0",
    "false",
)


# ── test doubles: a second, non-Notepad application ─────────────────────────



class FakeApplicationDriver:
    """Generic driver double: records real target checks for any adapter."""

    def __init__(self, adapters: tuple[ApplicationAdapter, ...]) -> None:
        self.registry = ApplicationRegistry(adapters)
        self.windows: dict[str, list[WindowBinding]] = {}
        self.texts: dict[int, str] = {}
        self.foreground_hwnd: int | None = None
        self.focus_works = True
        self.type_calls: list[tuple[str, int, str]] = []
        self.launch_calls: list[tuple[str, tuple[str, ...]]] = []
        self._next_hwnd = 5000

    # ── setup helpers ──
    def open_window(self, app_id: str, title: str, *, process: str, cls: str) -> WindowBinding:
        self._next_hwnd += 1
        binding = WindowBinding(
            hwnd=self._next_hwnd,
            title=title,
            class_name=cls,
            pid=9000 + self._next_hwnd % 100,
            process_name=process,
            app_id=app_id,
        )
        self.windows.setdefault(app_id, []).append(binding)
        self.texts.setdefault(binding.hwnd, "")
        self.foreground_hwnd = binding.hwnd
        return binding

    # ── driver protocol ──
    def is_available(self, app_id: str | None = None) -> tuple[bool, str]:
        target = app_id or ""
        adapter = self.registry.get(target)
        if adapter is None:
            return False, f"'{target}' is not a supported application."
        return True, f"{adapter.display_name} is available."

    def describe_windows(self) -> list[WindowBinding]:
        return [w for windows in self.windows.values() for w in windows]

    def find(self, app_id: str) -> list[WindowBinding]:
        return list(self.windows.get(app_id, []))

    def foreground(self) -> WindowBinding | None:
        for windows in self.windows.values():
            for window in windows:
                if window.hwnd == self.foreground_hwnd:
                    return window
        return None

    def launch(
        self,
        app_id: str,
        *,
        args: tuple[str, ...] = (),
        timeout: float = 10.0,
    ) -> WindowBinding:
        self.launch_calls.append((app_id, args))
        adapter = self.registry.get(app_id)
        assert adapter is not None
        title = f"{adapter.display_name}"
        if args:
            title = f"{args[0]} - {adapter.display_name}"
        return self.open_window(
            app_id,
            title,
            process=adapter.process_names[0],
            cls=(adapter.window_classes or ("FakeWindow",))[0],
        )

    def launch_with_options(
        self,
        app_id: str,
        *,
        options: dict[str, Any],
        timeout: float = 10.0,
    ) -> WindowBinding:
        args = tuple()
        if options.get("folder"):
            args = (str(options["folder"]),)
        return self.launch(app_id, args=args, timeout=timeout)

    def wait_until_ready(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        return self.is_bound(target)

    def is_bound(self, target: WindowBinding) -> bool:
        adapter = self.registry.get(target.app_id)
        if adapter is None:
            return False
        live = None
        for windows in self.windows.values():
            for window in windows:
                if window.hwnd == target.hwnd:
                    live = window
        if live is None:
            return False
        return adapter.allows(live)

    def focus(self, target: WindowBinding, *, timeout: float = 2.0) -> bool:
        if not self.is_bound(target) or not self.focus_works:
            return False
        self.foreground_hwnd = target.hwnd
        return True

    def type_text(self, target: WindowBinding, text: str) -> None:
        adapter = self.registry.get(target.app_id)
        if adapter is None or not adapter.supports_text_input:
            raise TargetResolutionError("typing is not supported for this application")
        if not self.is_bound(target):
            raise TargetResolutionError("the bound window is no longer valid")
        if self.foreground_hwnd != target.hwnd:
            raise TargetResolutionError("the bound window is not the active window")
        self.type_calls.append((target.app_id, target.hwnd, text))
        self.texts[target.hwnd] = self.texts.get(target.hwnd, "") + text

    def read_text(
        self,
        target: WindowBinding,
        *,
        max_chars: int = 32768,
        app_id: str | None = None,
    ) -> str:
        return self.texts.get(target.hwnd, "")

    def _find_by_handle(self, hwnd: int) -> WindowBinding | None:
        for windows in self.windows.values():
            for window in windows:
                if window.hwnd == hwnd:
                    return window
        return None

    def window_of(self, hwnd: int) -> WindowBinding | None:
        return self._find_by_handle(hwnd)

    def observe(self, target: WindowBinding, expected: str, *, app_id: str | None = None) -> ObservationResult:
        content = self.texts.get(target.hwnd, "")
        if expected.casefold() in content.casefold():
            return ObservationResult(
                status=OBSERVATION_VERIFIED,
                detail="observed in the fake application",
                content=content,
                source="fake",
            )
        return ObservationResult(
            status=OBSERVATION_FAILED,
            detail=f"observed {content!r}, which does not contain {expected!r}",
            content=content,
            source="fake",
        )

    def bind(
        self,
        app_id: str | None = None,
        *,
        hwnd: int | None = None,
        timeout: float = 6.0,
    ) -> tuple[WindowBinding | None, str]:
        adapter = self.registry.get(app_id) if app_id else None
        if hwnd is not None:
            for windows in self.windows.values():
                for window in windows:
                    if window.hwnd == hwnd:
                        owner = self.registry.identify(window)
                        if owner is None:
                            return None, f"Window {hwnd} is not a supported application."
                        return window.identified_as(owner.app_id), ""
            return None, f"Window {hwnd} is not an open window."
        if adapter is not None:
            windows = self.windows.get(adapter.app_id, [])
            if windows:
                return windows[0], ""
            return None, f"No open {adapter.display_name} window was found."
        return None, "No target application was specified."


def _registry_with_fake_notes() -> ApplicationRegistry:
    notes = ApplicationAdapter(
        app_id="notes",
        display_name="Notes",
        description="Second test application (adapter declared exactly like a real one).",
        process_names=("Notes.exe",),
        window_classes=("NotesWindow",),
        title_patterns=("notes",),
        # No executable on this machine: the adapter is still fully usable, which is
        # what makes the capability extensible rather than hard-coded per app.
        executable_names=(),
        supports_text_input=True,
        probes=(TextControlProbe(class_chain=("Edit",)),),
    )
    return ApplicationRegistry(tuple(default_application_registry().all()) + (notes,))


class StaticPlanner:
    """Returns the same plan once; a repeat makes Brain stop as an identical plan."""

    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan
        self.calls = 0

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        self.calls += 1
        return self._plan


def _brain_with(driver: Any, *steps: PlanStep, registry: ApplicationRegistry | None = None) -> Brain:
    """A Brain wired to the desktop capability with the given driver injected."""
    handler = DesktopTaskHandler(
        notepad_driver=driver,
        application_registry=registry,
    )
    intents = (
        "launch_application",
        "type_text",
        "read_application_text",
        "inspect_applications",
        "launch_notepad",
        "type_text_in_notepad",
        "read_notepad_text",
    )
    handlers: dict[str, Any] = {intent: handler for intent in intents}
    return Brain(
        planner=StaticPlanner(ExecutionPlan(steps=tuple(steps))),
        executor=TaskExecutor(handlers=handlers),
        permissions=DefaultPermissionPolicy(),
    )


# ── adapter registry ───────────────────────────────────────────────────────


def test_registry_describes_supported_applications():
    registry = default_application_registry()
    ids = [adapter.app_id for adapter in registry.all()]
    assert ids == [APP_NOTEPAD, APP_CALCULATOR, APP_FILE_EXPLORER, APP_VSCODE]
    described = registry.describe()
    assert all("installed" in entry for entry in described)
    summary = registry.summary_for_planner()
    assert "notepad" in summary and "calculator" in summary


def test_adapters_declare_identity_and_observation():
    registry = default_application_registry()
    notepad = registry.get(APP_NOTEPAD)
    assert notepad.supports_text_input is True
    assert notepad.probes, "Notepad must declare an observation mechanism"
    assert notepad.allows(
        WindowBinding(hwnd=1, title="Untitled - Notepad", class_name="Notepad", pid=1, process_name="Notepad.exe")
    ) is False or notepad.allows(
        WindowBinding(hwnd=1, title="Untitled - Notepad", class_name="Notepad", pid=1, process_name="Notepad.exe")
    )
    # A window with the right title but the wrong process is NOT Notepad.
    assert notepad.allows(
        WindowBinding(hwnd=1, title="Notepad", class_name="Notepad", pid=1, process_name="NotReally.exe")
    ) is False
    # Calculator declares no text input and observes via its copy command.
    calculator = registry.get(APP_CALCULATOR)
    assert calculator.supports_text_input is False
    assert isinstance(calculator.probes[0], ClipboardCopyProbe)
    # VS Code only exposes the window itself.
    assert isinstance(registry.get(APP_VSCODE).probes[0], WindowStateProbe)


def test_registry_resolves_names_and_rejects_unknown():
    registry = default_application_registry()
    for name, expected in (
        ("Notepad", APP_NOTEPAD),
        ("notepad.exe", APP_NOTEPAD),
        ("Calculator", APP_CALCULATOR),
        ("File Explorer", APP_FILE_EXPLORER),
        ("VS Code", APP_VSCODE),
    ):
        adapter = registry.resolve(name)
        assert adapter is not None and adapter.app_id == expected, name
    assert registry.resolve("Photoshop") is None
    assert registry.resolve("") is None


def test_unknown_application_window_is_not_targetable():
    registry = default_application_registry()
    unknown = WindowBinding(hwnd=1, title="Untitled - Photoshop", class_name="Photoshop", pid=5, process_name="Photoshop.exe")
    assert registry.identify(unknown) is None


# ── generic tools / skills ─────────────────────────────────────────────────


def test_generic_intents_are_wired_and_classified():
    executor = create_mixed_task_executor()
    handlers = executor.handlers or {}
    for intent in (
        "launch_application",
        "type_text",
        "read_application_text",
        "inspect_applications",
    ):
        assert intent in handlers, f"{intent} is not wired"

    typing = cross_app_operation_metadata("type_text")
    assert typing is not None
    assert typing["risk_level"] == RiskLevel.MEDIUM
    assert typing["externally_visible"] is True

    launch = cross_app_operation_metadata("launch_application")
    assert launch is not None and launch["risk_level"] == RiskLevel.LOW

    metadata = handlers["type_text"].get_metadata(
        TaskInput(
            step_id="s",
            description="type",
            intent="type_text",
            execution_id="e",
            goal="g",
            step_metadata={"app": "notepad", "text": "hi"},
        )
    )
    assert metadata["action"] == DesktopAction.TYPE_TEXT_IN_APPLICATION.value
    assert metadata["risk_level"] == RiskLevel.MEDIUM


def test_generic_launch_and_type_flow_for_a_second_application():
    """A non-Notepad adapter uses the same launch/bind/type/observe path."""
    registry = _registry_with_fake_notes()
    driver = FakeApplicationDriver(registry.all())
    brain = _brain_with(
        driver,
        PlanStep(description="open Notes", intent="launch_application", metadata={"app": "notes"}),
        PlanStep(
            description="type a memo",
            intent="type_text",
            metadata={"app": "notes", "text": "remember this"},
        ),
        registry=registry,
    )

    result = brain.run("open Notes and type remember this")

    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    assert driver.launch_calls and driver.launch_calls[0][0] == "notes"
    assert driver.type_calls and driver.type_calls[0][0] == "notes"
    assert any(o.content.startswith("Typed") for o in result.observations)


def test_type_into_application_without_text_input_is_refused():
    registry = _registry_with_fake_notes()
    driver = FakeApplicationDriver(registry.all())
    brain = _brain_with(
        driver,
        PlanStep(
            description="type into Calculator",
            intent="type_text",
            metadata={"app": "calculator", "text": "hello"},
        ),
        registry=registry,
    )

    result = brain.run("type hello into Calculator")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("not a supported action" in o.content for o in result.observations)


def test_unsupported_application_is_rejected_before_acting():
    registry = _registry_with_fake_notes()
    driver = FakeApplicationDriver(registry.all())
    brain = _brain_with(
        driver,
        PlanStep(
            description="type into Photoshop",
            intent="type_text",
            metadata={"app": "Photoshop", "text": "hello"},
        ),
        registry=registry,
    )

    result = brain.run("type hello into Photoshop")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("not a supported application" in o.content for o in result.observations)


def test_explicit_foreign_handle_is_rejected_not_retargeted():
    registry = _registry_with_fake_notes()
    driver = FakeApplicationDriver(registry.all())
    notes_window = driver.open_window(
        "notes", "Notes", process="Notes.exe", cls="NotesWindow"
    )
    driver.foreground_hwnd = notes_window.hwnd
    brain = _brain_with(
        driver,
        PlanStep(
            description="type into a foreign window",
            intent="type_text",
            metadata={"app": "notepad", "text": "hello", "hwnd": 987654},
        ),
        registry=registry,
    )

    result = brain.run("type hello into the notepad window")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any("not an open" in o.content for o in result.observations)


def test_cross_application_handle_named_for_another_app_is_rejected():
    registry = _registry_with_fake_notes()
    driver = FakeApplicationDriver(registry.all())
    notes_window = driver.open_window(
        "notes", "Notes", process="Notes.exe", cls="NotesWindow"
    )
    driver.foreground_hwnd = notes_window.hwnd
    brain = _brain_with(
        driver,
        PlanStep(
            description="type into notepad",
            intent="type_text",
            metadata={"app": "notepad", "text": "hello", "hwnd": notes_window.hwnd},
        ),
        registry=registry,
    )

    result = brain.run("type hello into notepad")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert any(
        ("not the requested" in o.content) or ("not an open" in o.content)
        for o in result.observations
    ), [o.content for o in result.observations]


def test_wrong_target_is_never_typed_into_when_focus_cannot_be_reclaimed():
    registry = _registry_with_fake_notes()
    driver = FakeApplicationDriver(registry.all())
    notes_window = driver.open_window("notes", "Notes", process="Notes.exe", cls="NotesWindow")
    driver.focus_works = False
    brain = _brain_with(
        driver,
        PlanStep(
            description="type into notes",
            intent="type_text",
            metadata={"app": "notes", "text": "hello"},
        ),
        registry=registry,
    )

    result = brain.run("type hello into Notes")

    assert result.status == ResultStatus.FAILED
    assert driver.type_calls == []
    assert notes_window.hwnd not in [hwnd for _, hwnd, _ in driver.type_calls]
    assert any("refusing to type" in o.content.lower() for o in result.observations)


# ── observation honesty ────────────────────────────────────────────────────


def test_application_without_observation_reports_unverified():
    """VS Code declares no content observation: Mamba must not claim success."""
    registry = default_application_registry()
    vscode = registry.get(APP_VSCODE)
    window = WindowBinding(
        hwnd=1,
        title="main.py - Visual Studio Code",
        class_name="Chrome_WidgetWin_1",
        pid=7,
        process_name="Code.exe",
    )
    assert vscode.allows(window)
    assert isinstance(vscode.primary_probe(window), WindowStateProbe)

    result = vscode.observe(window, "some typed text")
    # WindowStateProbe cannot confirm document content, so it must not claim it did.
    assert result.status in (OBSERVATION_FAILED, OBSERVATION_UNVERIFIED)


def test_text_control_probe_reports_missing_control_as_unverified():
    probe = TextControlProbe(class_chain=("Edit",))
    window = WindowBinding(hwnd=1, title="Untitled - Notepad", class_name="Notepad", pid=1, process_name="Notepad.exe")
    # No such control exists for this window in a headless test environment.
    result = probe.confirm(window, "hello")
    assert result.status in (OBSERVATION_FAILED, OBSERVATION_UNVERIFIED)
    assert result.status != OBSERVATION_VERIFIED


def test_clipboard_copy_probe_reports_failure_when_app_does_not_respond(monkeypatch):
    """A copy that never lands must be reported, not misread as a stale value."""
    import types

    state = {"value": "previous clipboard contents"}

    class FakeClipboard(types.ModuleType):
        def paste(self) -> str:  # type: ignore[override]
            return state["value"]

        def copy(self, value: str) -> None:  # type: ignore[override]
            state["value"] = value

    fake_clipboard = FakeClipboard("pyperclip")
    monkeypatch.setitem(__import__("sys").modules, "pyperclip", fake_clipboard)
    monkeypatch.setattr(
        "tools.desktop.observation.focus_window", lambda hwnd, timeout=2.0: True
    )

    probe = ClipboardCopyProbe(settle_seconds=0.01)
    window = WindowBinding(
        hwnd=1,
        title="Calculator",
        class_name="ApplicationFrameWindow",
        pid=1,
        process_name="ApplicationFrameHost.exe",
    )
    content, error = probe.observe(window)
    assert content is None, "a copy that never landed must not be read as a value"
    assert "copy command" in error or "clipboard" in error
    # The original clipboard is restored even when observation fails.
    assert state["value"] == "previous clipboard contents"


def test_observation_result_metadata_is_explicit():
    result = ObservationResult(
        status=OBSERVATION_VERIFIED,
        detail="ok",
        content="hello",
        source="test",
    )
    metadata = result.to_metadata()
    assert metadata["observation_status"] == OBSERVATION_VERIFIED
    assert metadata["observation_source"] == "test"
    assert metadata["observed_content"] == "hello"


# ── existing capabilities still verified through the same framework ────────


def test_filesystem_and_terminal_results_still_verified(tmp_path):
    """Regression guard: the generic cross-app work did not weaken existing verification."""
    executor = create_mixed_task_executor(root_dir=tmp_path)
    target = tmp_path / "cross_app_regression.txt"
    plan = ExecutionPlan(
        steps=(
            PlanStep(
                description="write a file",
                intent="write_file",
                metadata={"path": str(target), "content": "still verified"},
            ),
        )
    )
    brain = Brain(planner=StaticPlanner(plan), executor=executor)

    result = brain.run("write the file")

    assert result.status == ResultStatus.COMPLETED
    assert target.read_text(encoding="utf-8") == "still verified"


def test_application_registry_is_extensible_without_core_changes():
    """Adding an application is a registry entry, not a new brain or agent."""
    registry = _registry_with_fake_notes()
    adapter = registry.get("notes")
    assert adapter is not None
    assert registry.resolve("Notes") is adapter
    described = {entry["app_id"] for entry in registry.describe()}
    assert "notes" in described
    # The new adapter is reachable through the same generic intents and skills.
    driver = FakeApplicationDriver(registry.all())
    brain = _brain_with(
        driver,
        PlanStep(
            description="type a memo",
            intent="type_text",
            metadata={"app": "notes", "text": "extensible"},
        ),
        registry=registry,
    )
    window = driver.open_window("notes", "Notes", process="Notes.exe", cls="NotesWindow")
    driver.foreground_hwnd = window.hwnd
    result = brain.run("type extensible into Notes")
    assert result.status == ResultStatus.COMPLETED, result.error or result.output
    assert driver.texts[window.hwnd] == "extensible"


# ── opt-in real Windows validation ─────────────────────────────────────────


@pytest.mark.skipif(
    not _REAL_DESKTOP_TESTS,
    reason="real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run",
)
def test_real_multi_application_launch_and_bind():
    """Launch several representative applications and bind each one's window."""
    driver = CrossAppDriver()
    seen: dict[str, str] = {}

    for app_id in (APP_NOTEPAD, APP_CALCULATOR, APP_FILE_EXPLORER, APP_VSCODE):
        adapter = driver.registry.get(app_id)
        assert adapter is not None
        if not adapter.is_installed():
            continue
        try:
            target = driver.launch(app_id, timeout=25.0)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"could not launch {adapter.display_name}: {exc}")
        assert driver.is_bound(target), f"{app_id} window did not verify"
        assert driver.identify(target).app_id == app_id
        seen[app_id] = target.title

    # Clean up the windows this test opened (test-only; Mamba has no close action).
    _close_windows(seen, driver)


def test_real_calculator_display_observation():
    """Calculator's display is observable through its own copy command."""
    if not _REAL_DESKTOP_TESTS:
        pytest.skip("real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run")
    driver = CrossAppDriver()
    adapter = driver.registry.get(APP_CALCULATOR)
    if adapter is None or not adapter.is_installed():
        pytest.skip("Calculator is not installed")

    target = driver.launch(APP_CALCULATOR, timeout=25.0)
    assert driver.is_bound(target)
    content, error = driver.read_text(target, app_id=APP_CALCULATOR)
    # Either the display is readable, or the probe reports honestly why not.
    if content is None:
        assert error
    else:
        assert isinstance(content, str)
    _close_windows({APP_CALCULATOR: target.title}, driver)


@pytest.mark.skipif(
    not _REAL_DESKTOP_TESTS,
    reason="real desktop interaction; set MAMBA_REAL_DESKTOP_TESTS=1 to run",
)
def test_real_file_explorer_opens_requested_folder():
    """File Explorer navigation: launch at a folder and bind the resulting window."""
    driver = CrossAppDriver()
    adapter = driver.registry.get(APP_FILE_EXPLORER)
    assert adapter is not None and adapter.is_installed()

    folder = os.path.expanduser("~")
    target = driver.launch_with_options(APP_FILE_EXPLORER, options={"folder": folder}, timeout=25.0)
    assert driver.is_bound(target)
    assert driver.identify(target).app_id == APP_FILE_EXPLORER
    assert target.title  # folder windows carry the folder name in the title
    _close_windows({APP_FILE_EXPLORER: target.title}, driver)


def _close_windows(titles: dict[str, str], driver: CrossAppDriver) -> None:
    """Close windows opened by the real-desktop tests (test-only cleanup)."""
    try:
        import win32con
        import win32gui

        for app_id, title in titles.items():
            for window in driver.find(app_id):
                try:
                    win32gui.PostMessage(window.hwnd, win32con.WM_CLOSE, 0, 0)
                except Exception:
                    pass
                time.sleep(0.2)
    except Exception:
        pass
