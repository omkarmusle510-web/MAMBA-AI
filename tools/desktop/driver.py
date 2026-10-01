"""Generic cross-application driver for Mamba.

One driver serves every supported application. The application-specific part
lives in the adapter registry (:mod:`tools.desktop.apps`); the driver only knows
how to bind a target, focus it, type into it, and observe it.

Safety contract (this is the whole point of the module):

* **Bind before acting.** A target is a :class:`WindowBinding` that positively
  identifies as the requested supported application: expected process name,
  window class, and title. A handle that does not verify is rejected — never
  swapped for the current foreground window.
* **Re-verify at the moment of use.** Before a keystroke is sent, the binding
  must still be live and must still be the *active* window. Otherwise the action
  stops.
* **Observe, don't assume.** Outcomes are read back through the adapter's
  declared observation probe; if none can observe, the result is reported
  unverified.
* **No arbitrary targets.** Typing requires the adapter to declare
  ``supports_text_input``; applications without a positive identity checks are
  not targetable at all.
* **No destructive operations.** The driver launches, focuses, types, and reads.
  It never closes, deletes, or overwrites anything.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from . import observation as obs
from ._win32 import (
    DesktopUnavailableError,
    PyAutoGuiTextEntry,
    TargetResolutionError,
    TextEntry,
    WindowBinding,
    enumerate_windows,
    focus_window,
    foreground_window,
    is_windows,
    window_of,
)
from .apps import ApplicationAdapter, ApplicationRegistry, default_application_registry

_DEFAULT_LAUNCH_TIMEOUT = 12.0
_DEFAULT_FOCUS_TIMEOUT = 2.0
_DEFAULT_BIND_TIMEOUT = 6.0


class ApplicationUnavailableError(RuntimeError):
    """Raised when an application cannot be used on this machine at all."""


@dataclass(slots=True)
class BoundApplicationDriver:
    """One adapter's view of a :class:`CrossAppDriver`.

    Gives application-scoped call sites (and tests) a compact surface —
    ``is_available / describe_windows / launch / focus / foreground / type_text /
    read_text / is_bound / wait_for_editor / observe`` — without each of them
    re-implementing application resolution or target checks.
    """

    driver: CrossAppDriver
    app_id: str

    @property
    def adapter(self) -> ApplicationAdapter | None:
        return self.driver.registry.get(self.app_id)

    @property
    def display_name(self) -> str:
        adapter = self.adapter
        return adapter.display_name if adapter else self.app_id

    def is_available(self, app_id: str | None = None) -> tuple[bool, str]:
        return self.driver.is_available(app_id or self.app_id)

    def _mark(self, target: WindowBinding) -> WindowBinding:
        return target if target.app_id else target.identified_as(self.app_id)

    def find_windows(self) -> list[WindowBinding]:
        return self.driver.find(self.app_id)

    def describe_windows(self) -> list[WindowBinding]:
        return self.driver.find(self.app_id)

    def foreground(self) -> WindowBinding | None:
        return self.driver.foreground()

    def launch(
        self,
        *,
        timeout: float = _DEFAULT_LAUNCH_TIMEOUT,
        editor_timeout: float = 5.0,
        options: dict[str, Any] | None = None,
    ) -> WindowBinding:
        binding = self.driver.launch_with_options(
            self.app_id, options=options or {}, timeout=timeout
        )
        self.driver.wait_until_ready(binding, timeout=editor_timeout)
        return binding

    def wait_until_ready(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        return self.driver.wait_until_ready(self._mark(target), timeout=timeout)

    def wait_for_editor(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        return self.driver.wait_until_ready(self._mark(target), timeout=timeout)

    def is_bound(self, target: WindowBinding) -> bool:
        return self.driver.is_bound(self._mark(target))

    def focus(self, target: WindowBinding, *, timeout: float = _DEFAULT_FOCUS_TIMEOUT) -> bool:
        return self.driver.focus(self._mark(target), timeout=timeout)

    def type_text(self, target: WindowBinding, text: str) -> None:
        self.driver.type_text(self._mark(target), text)

    def read_text(
        self,
        target: WindowBinding,
        *,
        max_chars: int = 32768,
        app_id: str | None = None,
    ) -> str:
        content, error = self.driver.read_text(
            self._mark(target), app_id=app_id or self.app_id
        )
        if content is None:
            raise TargetResolutionError(error)
        return content

    def observe(self, target: WindowBinding, expected: str) -> obs.ObservationResult:
        return self.driver.observe(self._mark(target), expected, app_id=self.app_id)


@dataclass(slots=True)
class CrossAppDriver:
    """Generic launch / bind / focus / type / observe driver for adapters."""

    registry: ApplicationRegistry = field(default_factory=default_application_registry)
    text_entry: TextEntry = field(default_factory=PyAutoGuiTextEntry)
    _bound_app_id: str | None = None

    # ── availability / discovery ──

    def is_available(self, app_id: str | None = None) -> tuple[bool, str]:
        """Whether a supported application can be used on this machine.

        Accepts an explicit application, or defaults to this driver's own
        application when it is scoped to one.
        """
        target = app_id or self._bound_app_id
        if target is None:
            return False, "No application was specified."
        if not is_windows():
            return False, "Application interaction is only supported on Windows."
        adapter = self.registry.get(target)
        if adapter is None:
            known = ", ".join(a.app_id for a in self.registry.all())
            return False, f"'{target}' is not a supported application (supported: {known})."
        try:
            from ._win32 import import_win32

            import_win32()
        except DesktopUnavailableError as exc:
            return False, str(exc)
        if not adapter.is_installed():
            return False, (
                f"{adapter.display_name} was not found on this system "
                f"(looked for {', '.join(adapter.executable_names) or 'a declared path'})."
            )
        return True, f"{adapter.display_name} is available."

    def describe_application(self, app_id: str) -> dict[str, Any] | None:
        adapter = self.registry.get(app_id)
        return adapter.to_dict() if adapter is not None else None

    def discover(self) -> list[dict[str, Any]]:
        """Describe every supported application and whether it is present/open."""
        described: list[dict[str, Any]] = []
        for adapter in self.registry.all():
            entry = adapter.to_dict()
            entry["open_windows"] = [w.to_dict() for w in self.find(adapter.app_id)]
            described.append(entry)
        return described

    def find(self, app_id: str) -> list[WindowBinding]:
        """All open windows that positively identify as the application."""
        adapter = self.registry.get(app_id)
        if adapter is None:
            return []
        return [
            window.identified_as(adapter.app_id)
            for window in enumerate_windows()
            if adapter.allows(window)
        ]

    def find_all_supported(self) -> dict[str, list[WindowBinding]]:
        """Every open window Mamba recognises as a supported application."""
        return {adapter.app_id: self.find(adapter.app_id) for adapter in self.registry.all()}

    def describe_windows(self) -> list[WindowBinding]:
        """Windows of this driver's own application, when it is scoped to one."""
        if self._bound_app_id is None:
            return []
        return self.find(self._bound_app_id)

    def _find_by_handle(self, hwnd: int) -> WindowBinding | None:
        """Find a live window by handle, across every supported application."""
        live = window_of(int(hwnd))
        if live is None:
            return None
        adapter = self.registry.identify(live)
        return live.identified_as(adapter.app_id) if adapter is not None else None

    def foreground(self) -> WindowBinding | None:
        return foreground_window()

    # ── launch ──

    def launch(
        self,
        app_id: str,
        *,
        args: tuple[str, ...] = (),
        timeout: float = _DEFAULT_LAUNCH_TIMEOUT,
    ) -> WindowBinding:
        """Launch an application and bind the window that appears."""
        adapter = self.registry.get(app_id)
        if adapter is None:
            raise ApplicationUnavailableError(f"'{app_id}' is not a supported application.")

        available, reason = self.is_available(app_id)
        if not available:
            raise ApplicationUnavailableError(reason)

        executable = adapter.executable()
        if executable is None:  # pragma: no cover - is_available already checked
            raise ApplicationUnavailableError(f"{adapter.display_name} is not installed.")

        try:
            before = {w.hwnd for w in self.find(adapter.app_id)}
        except Exception:
            before = set()

        creation_flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creation_flags |= subprocess.DETACHED_PROCESS
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(  # noqa: S603 - declared adapter executable path
                [str(executable), *args],
                creationflags=creation_flags,
                close_fds=True,
            )
        except Exception as exc:
            raise ApplicationUnavailableError(
                f"Failed to launch {adapter.display_name}: {exc}"
            ) from exc

        deadline = time.monotonic() + max(0.5, timeout)
        newest: list[WindowBinding] = []
        while time.monotonic() < deadline:
            try:
                newest = [w for w in self.find(adapter.app_id) if w.hwnd not in before]
            except Exception:
                newest = []
            if newest:
                break
            time.sleep(0.15)

        if not newest:
            raise ApplicationUnavailableError(
                f"{adapter.display_name} was started but no {adapter.display_name} "
                f"window identified itself within {timeout:.0f}s."
            )

        chosen = newest[0]
        for binding in newest:
            if binding.title.lower().startswith("untitled"):
                chosen = binding
                break
        self.wait_until_ready(chosen, timeout=5.0)
        return chosen

    def wait_until_ready(self, target: WindowBinding, *, timeout: float = 5.0) -> bool:
        """Wait until a freshly launched window is ready to be observed/acted on.

        Windows applications create their window before their content controls
        exist (Windows 11 Notepad is a clear example); acting earlier would type
        into nothing or observe an empty state.
        """
        deadline = time.monotonic() + max(0.2, timeout)
        while True:
            if not self.is_bound(target):
                return False
            adapter = self.identify(target)
            if adapter is not None and (not adapter.probes or adapter.primary_probe(target)):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    # ── binding ──

    def identify(self, target: WindowBinding) -> ApplicationAdapter | None:
        return self.registry.identify(target)

    def is_bound(self, target: WindowBinding) -> bool:
        """Whether a binding still positively identifies as its application."""
        live = window_of(target.hwnd)
        if live is None or not live.title:
            return False
        if live.pid != target.pid:
            return False
        adapter = self.registry.get(target.app_id)
        if adapter is None:
            return False
        return adapter.allows(live)

    def bind(
        self,
        app_id: str | None = None,
        *,
        hwnd: int | None = None,
        timeout: float = _DEFAULT_BIND_TIMEOUT,
    ) -> tuple[WindowBinding | None, str]:
        """Resolve an explicitly declared application target.

        Resolution order:

        1. an explicit ``hwnd`` — accepted only if the window positively
           identifies as an application (and as ``app_id`` when given);
        2. an explicit ``app_id`` — its already-open window, if one exists;
        3. otherwise wait briefly for the application to open a window.

        Ambiguity or failure is returned as an error; the current foreground
        window is never substituted for the requested target.
        """
        requested = self.registry.get(app_id) if app_id else None
        if app_id and requested is None:
            known = ", ".join(a.app_id for a in self.registry.all())
            return None, f"'{app_id}' is not a supported application (supported: {known})."

        if hwnd is not None:
            candidate = window_of(int(hwnd))
            if candidate is None:
                return None, f"Window {hwnd} is not an open window."
            adapter = self.registry.identify(candidate)
            if adapter is None:
                return None, (
                    f"Window {hwnd} ('{candidate.title}') is not a supported "
                    "application window; refusing to act on an unverified target."
                )
            if requested is not None and adapter.app_id != requested.app_id:
                return None, (
                    f"Window {hwnd} is {adapter.display_name}, not the requested "
                    f"{requested.display_name}; refusing to act on the wrong target."
                )
            return candidate.identified_as(adapter.app_id), ""

        if requested is not None:
            deadline = time.monotonic() + max(0.2, timeout)
            while True:
                windows = self.find(requested.app_id)
                if windows:
                    return windows[0], ""
                if time.monotonic() >= deadline:
                    return None, (
                        f"No open {requested.display_name} window was found. Launch it "
                        "first, or specify which window to use."
                    )
                time.sleep(0.2)

        return None, (
            "No target application was specified. State which application to act on "
            "(for example Notepad)."
        )

    # ── action ──

    def focus(self, target: WindowBinding, *, timeout: float = _DEFAULT_FOCUS_TIMEOUT) -> bool:
        if not self.is_bound(target):
            return False
        return focus_window(target.hwnd, timeout=timeout)

    def type_text(self, target: WindowBinding, text: str) -> None:
        """Type text into a bound, verified-active application window."""
        if not text:
            raise TargetResolutionError("No text was provided to type.")

        adapter = self.registry.get(target.app_id)
        if adapter is None or not adapter.supports_text_input:
            name = adapter.display_name if adapter else "that application"
            raise TargetResolutionError(
                f"Refusing to type: typing text into {name} is not a supported action."
            )

        # Target binding re-check #1: still the bound supported application.
        if not self.is_bound(target):
            raise TargetResolutionError(
                "Refusing to type: the bound application window is no longer valid."
            )

        # Target binding re-check #2: it must be the active window right now.
        active = self.foreground()
        if active is None or active.hwnd != target.hwnd:
            current = (
                f"'{active.title}' ({active.process_name or active.class_name})"
                if active is not None
                else "an unknown window"
            )
            raise TargetResolutionError(
                f"Refusing to type: the active window is {current}, not the bound "
                f"{adapter.display_name} window '{target.title}'."
            )

        self.text_entry.type_text(text)

    # ── observation ──

    def observe(self, target: WindowBinding, expected: str, *, app_id: str | None = None) -> obs.ObservationResult:
        """Observe whether an expected outcome occurred in the bound window."""
        adapter = self.registry.get(app_id or target.app_id)
        if adapter is None:
            return obs.ObservationResult(
                status=obs.OBSERVATION_UNVERIFIED,
                detail="the bound window does not identify as a supported application",
                source="none",
            )
        if not self.is_bound(target):
            return obs.ObservationResult(
                status=obs.OBSERVATION_UNVERIFIED,
                detail="the bound application window is no longer valid",
                source="none",
            )
        return adapter.observe(target, expected)

    def read_text(self, target: WindowBinding, *, app_id: str | None = None) -> tuple[str | None, str]:
        """Read the bound application's currently observable content."""
        adapter = self.registry.get(app_id or target.app_id)
        if adapter is None:
            return None, "the bound window does not identify as a supported application"
        if not self.is_bound(target):
            return None, "the bound application window is no longer valid"
        return adapter.read_content(target)

    # ── launch arguments ──

    def launch_arguments(self, adapter: ApplicationAdapter, options: dict[str, Any]) -> tuple[str, ...]:
        """Build generic launch arguments declared by an adapter."""
        if adapter.launch_argument_builder == "folder":
            folder = options.get("folder") or options.get("path")
            if folder:
                return (str(folder),)
        return adapter.launch_args

    def launch_with_options(
        self,
        app_id: str,
        *,
        options: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_LAUNCH_TIMEOUT,
    ) -> WindowBinding:
        adapter = self.registry.get(app_id)
        if adapter is None:
            raise ApplicationUnavailableError(f"'{app_id}' is not a supported application.")
        args = self.launch_arguments(adapter, options or {})
        return self.launch(app_id, args=args, timeout=timeout)


__all__ = [
    "ApplicationUnavailableError",
    "BoundApplicationDriver",
    "CrossAppDriver",
]
