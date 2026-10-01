"""Notepad cross-application tools for Mamba (Phase 9).

Thin tool layer over :mod:`tools.desktop.notepad`. Handlers validate inputs and
resolve the *explicitly bound* Notepad target; they contain no application logic
of their own beyond target binding and error reporting.
"""

from __future__ import annotations

from typing import Any

from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .notepad import (
    NotepadDriver,
    NotepadUnavailableError,
    TargetResolutionError,
    WindowBinding,
    WindowsNotepadDriver,
)
from .types import DESKTOP_OPERATIONS, DesktopAction

_DEFAULT_FOCUS_TIMEOUT = 2.0
_DEFAULT_EDITOR_TIMEOUT = 5.0
_MAX_NOTEPAD_READ_CHARS = 32768


def _wait_for_editor(driver: NotepadDriver, target: WindowBinding, *, timeout: float) -> bool:
    """Wait for a bound Notepad window's editor control, when the driver supports it."""
    waiter = getattr(driver, "wait_for_editor", None)
    if not callable(waiter):
        return True
    try:
        return bool(waiter(target, timeout=timeout))
    except Exception:
        return False


def _binding_from_metadata(meta: dict[str, Any]) -> WindowBinding | None:
    """Rebuild a window binding from step metadata, if a full binding is present."""
    hwnd = meta.get("hwnd")
    pid = meta.get("target_pid")
    if hwnd is None or pid is None:
        return None
    try:
        return WindowBinding(
            hwnd=int(hwnd),
            title=str(meta.get("target_title") or ""),
            class_name=str(meta.get("target_class") or "Notepad"),
            pid=int(pid),
            process_name=str(meta.get("target_process") or ""),
        )
    except (TypeError, ValueError):
        return None


def _binding_to_metadata(target: WindowBinding) -> dict[str, Any]:
    return {
        "app": "Notepad",
        "target_bound": True,
        "hwnd": target.hwnd,
        "target_title": target.title,
        "target_pid": target.pid,
        "target_process": target.process_name,
        "target_class": target.class_name,
    }


def _resolve_bound_target(
    driver: NotepadDriver,
    meta: dict[str, Any],
) -> tuple[WindowBinding | None, str]:
    """Resolve the Notepad target for a step, refusing to guess arbitrarily.

    Resolution order:
      1. an explicit ``hwnd`` supplied by the planner — accepted ONLY if that
         window independently re-verifies as a live Notepad window;
      2. an explicit full binding carried in step metadata (e.g. from a previous
         step in the same plan) — re-verified the same way;
      3. the current foreground window, if it is Notepad;
      4. the most recently enumerated visible Notepad window.

    Any explicitly supplied handle that does NOT verify as Notepad aborts
    resolution instead of silently falling back, so a stale or foreign target
    can never be used for an action.
    """
    explicit_hwnd = meta.get("hwnd")
    if explicit_hwnd is not None:
        try:
            raw_hwnd = int(explicit_hwnd)
        except (TypeError, ValueError):
            return None, f"Invalid target window handle: {explicit_hwnd!r}."
        for candidate in driver.find_windows():
            if candidate.hwnd == raw_hwnd:
                return candidate, ""
        return (
            None,
            f"The specified window (HWND {raw_hwnd}) is not an open Notepad window; "
            "refusing to act on an unverified target.",
        )

    bound = _binding_from_metadata(meta)
    if bound is not None:
        if driver.is_bound(bound):
            return bound, ""
        return (
            None,
            "The previously bound Notepad window is no longer valid; "
            "refusing to act on a stale target.",
        )

    try:
        foreground = driver.foreground()
    except Exception:
        foreground = None
    if foreground is not None and "notepad" in (foreground.process_name or "").lower():
        for candidate in driver.find_windows():
            if candidate.hwnd == foreground.hwnd:
                return candidate, ""

    windows = driver.find_windows()
    if windows:
        return windows[0], ""

    return (
        None,
        "No Notepad window is open. Open Notepad first, then repeat the request.",
    )


class LaunchNotepadHandler:
    """Handler for launching Notepad and binding the new window."""

    def __init__(self, driver: NotepadDriver | None = None) -> None:
        self._driver = driver or WindowsNotepadDriver()

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_NOTEPAD].to_metadata()

        available, reason = self._driver.is_available()
        if not available:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={**auth, "app": "Notepad", "available": False, "error": "notepad_unavailable"},
            )

        try:
            target = self._driver.launch()
        except NotepadUnavailableError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={**auth, "app": "Notepad", "error": "launch_failed"},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to launch Notepad: {exc}",
                metadata={**auth, "app": "Notepad", "error": "launch_failed"},
            )

        focused = False
        editor_ready = _wait_for_editor(
            self._driver, target, timeout=_DEFAULT_EDITOR_TIMEOUT
        )
        try:
            focused = self._driver.focus(target, timeout=_DEFAULT_FOCUS_TIMEOUT)
        except Exception:
            focused = False

        return ToolOutput(
            success=True,
            result=(
                f"Notepad launched and bound to window '{target.title}' "
                f"(HWND {target.hwnd}, PID {target.pid})."
                if focused
                else f"Notepad launched and bound to window '{target.title}' "
                f"(HWND {target.hwnd}, PID {target.pid}); focus was not confirmed."
            ),
            metadata={
                **auth,
                **_binding_to_metadata(target),
                "launched": True,
                "focused": focused,
                "editor_ready": editor_ready,
            },
        )


class TypeTextInNotepadHandler:
    """Handler for typing text into an explicitly bound Notepad window."""

    def __init__(self, driver: NotepadDriver | None = None) -> None:
        self._driver = driver or WindowsNotepadDriver()

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_NOTEPAD].to_metadata()
        meta = dict(input.metadata)
        meta.update(input.arguments)

        text = meta.get("text")
        if text is None:
            text = meta.get("content")
        if not isinstance(text, str) or not text.strip():
            return ToolOutput(
                success=False,
                error="Missing required argument: 'text'",
                metadata={**auth, "app": "Notepad", "error": "missing_text"},
            )

        available, reason = self._driver.is_available()
        if not available:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={**auth, "app": "Notepad", "available": False, "error": "notepad_unavailable"},
            )

        target, problem = _resolve_bound_target(self._driver, meta)
        if target is None:
            return ToolOutput(
                success=False,
                error=problem,
                metadata={
                    **auth,
                    "app": "Notepad",
                    "error": "target_not_resolved",
                    "target_bound": False,
                },
            )

        # Explicit rebinding before any keystroke: verify the resolved target is
        # still the live Notepad window and make it the active window.
        if not self._driver.is_bound(target):
            return ToolOutput(
                success=False,
                error="Refusing to type: the resolved Notepad window is no longer valid.",
                metadata={
                    **auth,
                    "app": "Notepad",
                    "error": "target_stale",
                    "target_bound": False,
                },
            )

        if not _wait_for_editor(self._driver, target, timeout=_DEFAULT_EDITOR_TIMEOUT):
            return ToolOutput(
                success=False,
                error=(
                    "Refusing to type: the bound Notepad window did not expose an "
                    "editable text control."
                ),
                metadata={
                    **auth,
                    **_binding_to_metadata(target),
                    "error": "editor_not_ready",
                },
            )

        if not self._driver.focus(target, timeout=_DEFAULT_FOCUS_TIMEOUT):
            foreground = None
            try:
                foreground = self._driver.foreground()
            except Exception:
                foreground = None
            active = (
                f"'{foreground.title}' ({foreground.process_name or 'unknown'})"
                if foreground is not None
                else "an unknown window"
            )
            return ToolOutput(
                success=False,
                error=(
                    "Refusing to type: the bound Notepad window could not be made the "
                    f"active window (active window is {active})."
                ),
                metadata={
                    **auth,
                    **_binding_to_metadata(target),
                    "error": "focus_failed",
                    "focused": False,
                },
            )

        try:
            self._driver.type_text(target, text)
        except TargetResolutionError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={**auth, **_binding_to_metadata(target), "error": "typing_refused"},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to type text into Notepad: {exc}",
                metadata={**auth, **_binding_to_metadata(target), "error": "typing_failed"},
            )

        return ToolOutput(
            success=True,
            result=(
                f"Typed {len(text)} character(s) into the bound Notepad window "
                f"'{target.title}' (HWND {target.hwnd})."
            ),
            metadata={
                **auth,
                **_binding_to_metadata(target),
                "focused": True,
                "typed": True,
                "text": text,
                "text_length": len(text),
            },
        )


class ReadNotepadTextHandler:
    """Handler for reading text back out of a bound Notepad window."""

    def __init__(self, driver: NotepadDriver | None = None) -> None:
        self._driver = driver or WindowsNotepadDriver()

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.READ_NOTEPAD_TEXT].to_metadata()
        meta = dict(input.metadata)
        meta.update(input.arguments)

        available, reason = self._driver.is_available()
        if not available:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={**auth, "app": "Notepad", "error": "notepad_unavailable"},
            )

        target, problem = _resolve_bound_target(self._driver, meta)
        if target is None:
            return ToolOutput(
                success=False,
                error=problem,
                metadata={"app": "Notepad", "error": "target_not_resolved", "target_bound": False},
            )

        try:
            text = self._driver.read_text(target, max_chars=_MAX_NOTEPAD_READ_CHARS)
        except TargetResolutionError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={**auth, **_binding_to_metadata(target), "error": "read_failed"},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to read Notepad text: {exc}",
                metadata={**auth, **_binding_to_metadata(target), "error": "read_failed"},
            )

        return ToolOutput(
            success=True,
            result=text,
            metadata={
                **auth,
                **_binding_to_metadata(target),
                "text": text,
                "text_length": len(text),
                "window_title": target.title,
            },
        )


class LaunchNotepadTool(BaseTool):
    def __init__(self, handler: LaunchNotepadHandler | None = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_NOTEPAD]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=handler or LaunchNotepadHandler(),
        )


class TypeTextInNotepadTool(BaseTool):
    def __init__(self, handler: TypeTextInNotepadHandler | None = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_NOTEPAD]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=handler or TypeTextInNotepadHandler(),
        )


class ReadNotepadTextTool(BaseTool):
    def __init__(self, handler: ReadNotepadTextHandler | None = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_NOTEPAD_TEXT]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=handler or ReadNotepadTextHandler(),
        )


__all__ = [
    "LaunchNotepadHandler",
    "LaunchNotepadTool",
    "ReadNotepadTextHandler",
    "ReadNotepadTextTool",
    "TypeTextInNotepadHandler",
    "TypeTextInNotepadTool",
]
