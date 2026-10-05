"""Shared Win32 window primitives for Mamba's desktop capabilities.

Single home for the low-level window operations used by every
cross-application adapter, so no adapter (and no skill) re-implements window
handling. Only the primitives that a generic cross-app capability needs live
here:

* enumerate visible top-level windows as :class:`WindowBinding` identities,
* read the current foreground window,
* make a specific window the active foreground window (with the standard
  Windows foreground-lock workaround),
* read text out of a Win32 text control (used for reliable observation), and
* a case-insensitive substring title matcher for declarative adapters.

Deliberately absent: any generic "control this application" API. Adapters bind
targets; nothing here can act on an arbitrary window.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_SW_RESTORE = 9
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002

MAX_TEXT_CHARS = 32768


class DesktopUnavailableError(RuntimeError):
    """Raised when a desktop operation is impossible on this machine."""


class TargetResolutionError(RuntimeError):
    """Raised when an intended target cannot be established safely.

    A wrong or stale target is never silently replaced, so this error always
    stops the action instead of falling back to "whatever is focused".
    """


@dataclass(frozen=True, slots=True)
class WindowBinding:
    """An explicitly bound top-level window identity.

    ``app_id`` names the supported application this window was verified as, when
    one was identified. An empty ``app_id`` means "not yet bound to an
    application" — such a window is never acted on.

    ``launched`` records provenance: True only when Mamba itself launched this
    window (ownership established at launch time). Windows adopted from the
    user's pre-existing desktop keep ``launched=False`` and are never treated
    as Mamba-owned.
    """

    hwnd: int
    title: str
    class_name: str
    pid: int
    process_name: str = ""
    app_id: str = ""
    launched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "pid": self.pid,
            "process_name": self.process_name,
            "app_id": self.app_id,
            "launched": self.launched,
        }

    def identified_as(self, app_id: str) -> "WindowBinding":
        """Return this binding tagged with the application it verified as."""
        return WindowBinding(
            hwnd=self.hwnd,
            title=self.title,
            class_name=self.class_name,
            pid=self.pid,
            process_name=self.process_name,
            app_id=app_id,
            launched=self.launched,
        )


def is_windows() -> bool:
    return sys.platform == "win32"


def require_windows() -> None:
    if not is_windows():
        raise DesktopUnavailableError(
            "Desktop application interaction is only supported on Windows."
        )


def import_win32():
    """Import the pywin32 modules, failing with a clear capability error."""
    require_windows()
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError as exc:  # pragma: no cover - depends on host install
        raise DesktopUnavailableError(
            f"pywin32 (win32gui) is required for desktop interaction: {exc}"
        ) from exc
    return win32con, win32gui, win32process


def process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return ""


class TextEntry:
    """Keyboard text entry used to type into the focused window."""

    def type_text(self, text: str) -> None: ...


class PyAutoGuiTextEntry:
    """Types text through the existing ``pyautogui`` dependency (SendInput).

    The inter-key interval matters: typing faster than the target application's
    input queue can drain silently drops characters (observed with Windows 11
    Notepad), so the default is deliberately unhurried.
    """

    def __init__(self, interval: float = 0.03) -> None:
        self._interval = interval

    def type_text(self, text: str) -> None:
        import pyautogui

        # Typing must never abort a task by tripping the mouse-corner failsafe.
        pyautogui.FAILSAFE = False
        pyautogui.write(text, interval=self._interval)


def resolve_executable(candidates: tuple[str, ...], names: tuple[str, ...]) -> Path | None:
    """Resolve the first existing executable among fixed paths and PATH lookups.

    Only absolute paths and PATH-resolved names are considered, so a planted
    executable cannot be silently launched under a trusted name unless it is
    genuinely what the OS resolves.
    """
    import shutil

    system_dirs = [
        os.path.join(os.environ.get("SystemRoot", ""), "System32"),
        os.path.join(os.environ.get("SystemRoot", "")),
    ]
    for name in names:
        for directory in system_dirs:
            if not directory:
                continue
            candidate = Path(directory) / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    for raw in candidates:
        if not raw:
            continue
        expanded = os.path.expandvars(os.path.expanduser(raw))
        candidate = Path(expanded)
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def title_matches(title: str, patterns: tuple[str, ...]) -> bool:
    """Case-insensitive "contains any pattern" test used by declarative adapters."""
    lowered = (title or "").lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def enumerate_windows() -> list[WindowBinding]:
    """Enumerate every visible top-level window as a binding."""
    _, win32gui, win32process = import_win32()
    out: list[WindowBinding] = []

    def _cb(hwnd: int, _: Any) -> bool:
        try:
            if win32gui.IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                out.append(
                    WindowBinding(
                        hwnd=hwnd,
                        title=win32gui.GetWindowText(hwnd) or "",
                        class_name=win32gui.GetClassName(hwnd),
                        pid=pid,
                        process_name=process_name(pid),
                    )
                )
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def window_of(hwnd: int) -> WindowBinding | None:
    """Return a binding for a live top-level window handle."""
    win32con, win32gui, win32process = import_win32()
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return WindowBinding(
            hwnd=hwnd,
            title=win32gui.GetWindowText(hwnd) or "",
            class_name=win32gui.GetClassName(hwnd),
            pid=pid,
            process_name=process_name(pid),
        )
    except Exception:
        return None


def foreground_window() -> WindowBinding | None:
    """Return a binding for the current foreground window, if any."""
    try:
        _, win32gui, _ = import_win32()
        hwnd = win32gui.GetForegroundWindow()
    except Exception:
        return None
    return window_of(hwnd) if hwnd else None


def focus_attempt(target_hwnd: int) -> None:
    """One attempt at making a window the active foreground window.

    Windows only permits the owner of the current foreground window's input
    queue to change focus, so this:

    1. attaches this thread to the foreground thread and the target's thread,
    2. sets focus and raises the target, then
    3. if ``SetForegroundWindow`` is still refused, retries with ALT held
       (the standard workaround for a background process restoring a window).

    It deliberately does NOT use ``SwitchToThisWindow``, which force-switches
    focus and would bypass Windows' own foreground protection. If the OS keeps
    declining, callers must fail the action rather than force input.
    """
    _, win32gui, win32process = import_win32()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    current_thread = 0
    attached: list[int] = []
    try:
        current_thread = kernel32.GetCurrentThreadId()
        try:
            foreground_hwnd = win32gui.GetForegroundWindow()
        except Exception:
            foreground_hwnd = 0

        for hwnd in (foreground_hwnd, target_hwnd):
            if not hwnd:
                continue
            try:
                thread_id, _ = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                continue
            if thread_id and thread_id != current_thread:
                try:
                    if win32process.AttachThreadInput(thread_id, current_thread, True):
                        attached.append(thread_id)
                except Exception:
                    pass

        try:
            win32gui.ShowWindow(target_hwnd, _SW_RESTORE)
        except Exception:
            pass
        try:
            user32.SetFocus(target_hwnd)
        except Exception:
            pass
        try:
            win32gui.BringWindowToTop(target_hwnd)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            try:
                user32.keybd_event(_VK_MENU, 0, 0, 0)
                time.sleep(0.02)
                win32gui.SetForegroundWindow(target_hwnd)
            except Exception:
                pass
            finally:
                try:
                    user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        for thread_id in attached:
            if thread_id and thread_id != current_thread:
                try:
                    win32process.AttachThreadInput(thread_id, current_thread, False)
                except Exception:
                    pass


def focus_window(target_hwnd: int, *, timeout: float = 2.0) -> bool:
    """Make a window active, polling until it is (or the timeout expires)."""
    deadline = time.monotonic() + max(0.2, timeout)
    while True:
        focus_attempt(target_hwnd)
        time.sleep(0.12)
        fg = foreground_window()
        if fg is not None and fg.hwnd == target_hwnd:
            return True
        if time.monotonic() >= deadline:
            return False


def find_child_by_chain(hwnd: int, class_chain: tuple[str, ...]) -> int | None:
    """Walk a chain of child window classes and return the final handle."""
    _, win32gui, _ = import_win32()
    current = hwnd
    for class_name in class_chain:
        try:
            child = win32gui.FindWindowEx(current, 0, class_name, None)
        except Exception:
            return None
        if not child:
            return None
        current = child
    return current


def find_child_by_classes(hwnd: int, classes: tuple[str, ...], *, max_depth: int = 6) -> int | None:
    """Breadth-first search for the first descendant window of a given class."""
    _, win32gui, _ = import_win32()
    queue: list[tuple[int, int]] = [(hwnd, 0)]
    while queue:
        parent, depth = queue.pop(0)
        if depth > max_depth:
            continue
        child = 0
        while True:
            try:
                child = win32gui.FindWindowEx(parent, child, None, None)
            except Exception:
                break
            if not child:
                break
            try:
                if win32gui.GetClassName(child) in classes:
                    return child
            except Exception:
                pass
            queue.append((child, depth + 1))
    return None


def read_control_text(control_hwnd: int, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Read text from a classic Win32 control via WM_GETTEXT.

    This is a cross-process-safe, read-only observation: it never sends input
    and never modifies the target application.
    """
    user32 = ctypes.windll.user32
    try:
        length = int(user32.SendMessageW(control_hwnd, _WM_GETTEXTLENGTH, 0, 0))
    except Exception as exc:
        raise TargetResolutionError(f"Failed to read control text length: {exc}") from exc
    if length <= 0:
        return ""

    limit = min(length, max(0, int(max_chars)))
    buffer = ctypes.create_unicode_buffer(limit + 2)
    try:
        user32.SendMessageW(control_hwnd, _WM_GETTEXT, limit + 1, ctypes.byref(buffer))
    except Exception as exc:
        raise TargetResolutionError(f"Failed to read control text: {exc}") from exc

    text = buffer.value
    if length > limit:
        text += "…"
    return text


def press_key(vk: int, *, ctrl: bool = False) -> None:
    """Send a single synthetic key press (used for adapter-declared probes)."""
    user32 = ctypes.windll.user32
    if ctrl:
        user32.keybd_event(0x11, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
    if ctrl:
        user32.keybd_event(0x11, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.08)


__all__ = [
    "MAX_TEXT_CHARS",
    "DesktopUnavailableError",
    "PyAutoGuiTextEntry",
    "TargetResolutionError",
    "TextEntry",
    "WindowBinding",
    "enumerate_windows",
    "find_child_by_chain",
    "find_child_by_classes",
    "focus_attempt",
    "focus_window",
    "foreground_window",
    "import_win32",
    "is_windows",
    "press_key",
    "process_name",
    "read_control_text",
    "require_windows",
    "resolve_executable",
    "title_matches",
    "window_of",
]
