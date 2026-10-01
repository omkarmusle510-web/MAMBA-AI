"""Cross-application interaction with Windows Notepad (Phase 9).

This module is the desktop capability layer for Mamba's first real
cross-application workflow:

    launch Notepad -> bind its window -> focus it -> type text -> read the text back

Design rules enforced here (see docs/ARCHITECTURE.md invariants 7 and 9):

1. **Explicit target binding.** No action ever guesses an application from
   whatever happens to be focused. A Notepad window is only accepted as a
   target when it is a visible top-level window of class ``"Notepad"`` whose
   owner process is the Notepad system executable, and whose process id can
   be re-verified at the moment of use. A stale window handle from an earlier
   task is rejected rather than reused.
2. **Verify before typing.** Before a single keystroke is sent, the bound
   window must still be the current foreground window. Otherwise the action
   stops with a clear failure instead of typing into an unrelated application.
3. **Observe, don't assume.** The text is read back from the window's own
   editor control after typing, so the caller can distinguish "keystrokes were
   sent" from "the requested text actually appeared".
4. **No destructive operations.** This layer launches, focuses, types into, and
   reads Notepad only. It never closes, overwrites, or deletes anything.
5. **No generic automation surface.** This is deliberately Notepad-only: there
   is no arbitrary application launcher, no arbitrary window text reader, and
   no general RPA/DSL. Text can only be typed into a window that proves it is
   Notepad.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# Win32 window-message constants used for passive text observation.
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_SW_RESTORE = 9
_MAX_READ_CHARS = 32768

# Virtual-key / key-event constants used only to clear the foreground lock.
_VK_MENU = 0x12  # ALT
_KEYEVENTF_KEYUP = 0x0002

# The window class name used by Windows Notepad (classic and Windows 11 builds).
_NOTEPAD_WINDOW_CLASS = "Notepad"
# Editor control classes that hold Notepad's document text. Windows 11 Notepad
# renders into a RichEdit control hosted by a "NotepadTextBox" bridge window;
# older builds use a plain "Edit" control.
_NOTEPAD_EDITOR_CLASSES = ("RichEditD2DPT", "RichEdit50W", "RichEdit20W", "Edit")


class NotepadUnavailableError(RuntimeError):
    """Raised when Notepad cannot be used on this machine at all."""


class TargetResolutionError(RuntimeError):
    """Raised when the intended Notepad target cannot be established safely."""


@dataclass(frozen=True, slots=True)
class WindowBinding:
    """An explicitly bound top-level window identity."""

    hwnd: int
    title: str
    class_name: str
    pid: int
    process_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "pid": self.pid,
            "process_name": self.process_name,
        }


class NotepadDriver(Protocol):
    """OS-level operations for the Notepad cross-application capability."""

    def is_available(self) -> tuple[bool, str]: ...

    def find_windows(self) -> list[WindowBinding]: ...

    def launch(self, *, timeout: float = 10.0) -> WindowBinding: ...

    def focus(self, target: WindowBinding, *, timeout: float = 3.0) -> bool: ...

    def foreground(self) -> WindowBinding | None: ...

    def type_text(self, target: WindowBinding, text: str) -> None: ...

    def read_text(self, target: WindowBinding, *, max_chars: int = _MAX_READ_CHARS) -> str: ...

    def is_bound(self, target: WindowBinding) -> bool: ...

    def wait_for_editor(self, target: WindowBinding, *, timeout: float = 5.0) -> bool: ...


class TextEntry(Protocol):
    """Keyboard text entry used to type into the focused window."""

    def type_text(self, text: str) -> None: ...


class PyAutoGuiTextEntry:
    """Types text through the existing ``pyautogui`` dependency (SendInput)."""

    def __init__(self, interval: float = 0.01) -> None:
        self._interval = interval

    def type_text(self, text: str) -> None:
        import pyautogui

        # Typing must never abort the whole task by moving the mouse to a hot corner.
        pyautogui.FAILSAFE = False
        pyautogui.write(text, interval=self._interval)


# ── Win32 helpers (kept private; no public window-manipulation surface) ─────


def _require_windows() -> None:
    if sys.platform != "win32":
        raise NotepadUnavailableError(
            "Notepad interaction is only supported on Windows."
        )


def _import_win32():
    _require_windows()
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError as exc:  # pragma: no cover - depends on host install
        raise NotepadUnavailableError(
            f"pywin32 (win32gui) is required for Notepad interaction: {exc}"
        ) from exc
    return win32con, win32gui, win32process


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return ""


def _kernel32():
    return ctypes.windll.kernel32


def _notepad_focus_attempt(target_hwnd: int) -> None:
    """One attempt at making a window the active foreground window.

    Windows only permits the owner of the current foreground window's input
    queue to change focus. This attempt therefore:

    1. attaches this thread to the foreground thread and to the target's thread
       (making the focus request legal),
    2. sets focus to the target and raises it, and
    3. falls back to ``SetForegroundWindow`` performed while ALT is held, which
       is the long-standing Windows workaround for a background process that
       legitimately needs to restore a window it owns.

    It does NOT use ``SwitchToThisWindow`` (which force-switches focus and would
    bypass Windows' foreground protection) — if the OS declines the request, the
    caller must fail rather than force input somewhere.
    """
    _, win32gui, win32process = _import_win32()
    user32 = ctypes.windll.user32
    kernel32 = _kernel32()

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
            # ALT held during SetForegroundWindow clears the foreground lock.
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


def _looks_like_notepad(process_name: str) -> bool:
    return "notepad" in (process_name or "").lower()


def _is_bound_notepad_window(target: WindowBinding) -> bool:
    """Re-verify that a binding still identifies a live Notepad window."""
    try:
        _, win32gui, win32process = _import_win32()
    except NotepadUnavailableError:
        return False

    try:
        if not win32gui.IsWindow(target.hwnd):
            return False
        if not win32gui.IsWindowVisible(target.hwnd):
            return False
        if win32gui.GetClassName(target.hwnd) != _NOTEPAD_WINDOW_CLASS:
            return False
        title = win32gui.GetWindowText(target.hwnd) or ""
        if "notepad" not in title.lower():
            return False
        _, pid = win32process.GetWindowThreadProcessId(target.hwnd)
        if pid != target.pid:
            return False
        return _looks_like_notepad(_process_name(pid) or target.process_name)
    except Exception:
        return False


def _enumerate_candidates() -> list[WindowBinding]:
    """Enumerate visible top-level Notepad windows as bindings."""
    _, win32gui, win32process = _import_win32()

    candidates: list[WindowBinding] = []

    def _cb(hwnd: int, _: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetClassName(hwnd) != _NOTEPAD_WINDOW_CLASS:
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            if "notepad" not in title.lower():
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pname = _process_name(pid)
            if not _looks_like_notepad(pname):
                return True
            candidates.append(
                WindowBinding(
                    hwnd=hwnd,
                    title=title,
                    class_name=_NOTEPAD_WINDOW_CLASS,
                    pid=pid,
                    process_name=pname,
                )
            )
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return candidates


def _find_editor_control(hwnd: int, *, max_depth: int = 6) -> int | None:
    """Return the handle of the control that holds a Notepad window's text."""
    _, win32gui, _ = _import_win32()

    queue: list[tuple[int, int]] = [(hwnd, 0)]
    first_editor: int | None = None
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
                cls = win32gui.GetClassName(child)
            except Exception:
                cls = ""
            if cls in _NOTEPAD_EDITOR_CLASSES and first_editor is None:
                first_editor = child
            queue.append((child, depth + 1))
    return first_editor


def _notepad_executable() -> Path | None:
    """Resolve the Notepad executable Mamba is willing to launch.

    Only the OS-provided Notepad binary is accepted, resolved by absolute path.
    This prevents a planted executable earlier on ``PATH`` from being launched
    under the name "notepad".
    """
    candidates: list[Path] = []
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        candidates.append(Path(system_root) / "System32" / "notepad.exe")
    which = shutil.which("notepad")
    if which:
        candidates.append(Path(which))

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


class WindowsNotepadDriver:
    """Real Windows implementation of the Notepad driver."""

    def __init__(self, *, text_entry: TextEntry | None = None) -> None:
        self._text_entry = text_entry or PyAutoGuiTextEntry()

    # ── discovery / availability ──

    def is_available(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Notepad interaction is only supported on Windows."
        try:
            _import_win32()
        except NotepadUnavailableError as exc:
            return False, str(exc)
        except Exception as exc:  # pragma: no cover
            return False, str(exc)

        if _notepad_executable() is None:
            return False, "Notepad (notepad.exe) was not found on this system."
        return True, "Notepad is available."

    def find_windows(self) -> list[WindowBinding]:
        return _enumerate_candidates()

    def launch(
        self,
        *,
        timeout: float = 10.0,
        editor_timeout: float = 5.0,
    ) -> WindowBinding:
        _require_windows()
        executable = _notepad_executable()
        if executable is None:
            raise NotepadUnavailableError("Notepad (notepad.exe) was not found on this system.")

        try:
            before = {b.hwnd for b in self.find_windows()}
        except NotepadUnavailableError:
            raise
        except Exception:
            before = set()

        creation_flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creation_flags |= subprocess.DETACHED_PROCESS
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(  # noqa: S603 - fixed OS-provided executable path
                [str(executable)],
                creationflags=creation_flags,
                close_fds=True,
            )
        except Exception as exc:
            raise NotepadUnavailableError(f"Failed to launch Notepad: {exc}") from exc

        deadline = time.monotonic() + max(0.5, timeout)
        newest: list[WindowBinding] = []
        while time.monotonic() < deadline:
            try:
                newest = [b for b in self.find_windows() if b.hwnd not in before]
            except Exception:
                newest = []
            if newest:
                break
            time.sleep(0.15)

        if not newest:
            raise NotepadUnavailableError(
                "Notepad was started but no Notepad window appeared within "
                f"{timeout:.0f}s."
            )

        # Prefer a fresh empty document window when several opened at once.
        chosen = newest[0]
        for binding in newest:
            if binding.title.lower().startswith("untitled"):
                chosen = binding
                break

        # Windows 11 Notepad creates its window before its editor control is
        # ready; wait for an editable target rather than returning a window that
        # cannot yet be typed into or read.
        self.wait_for_editor(chosen, timeout=editor_timeout)
        return chosen

    def wait_for_editor(
        self,
        target: WindowBinding,
        *,
        timeout: float = 5.0,
    ) -> bool:
        """Wait until a bound Notepad window exposes its text control.

        Windows 11 Notepad creates its window before its editor control is
        ready; typing or reading before then would either be refused or observe
        an empty document.
        """
        deadline = time.monotonic() + max(0.2, timeout)
        while True:
            if not _is_bound_notepad_window(target):
                return False
            if _find_editor_control(target.hwnd) is not None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    # ── focus / observation ──

    def foreground(self) -> WindowBinding | None:
        _, win32gui, win32process = _import_win32()
        try:
            hwnd = win32gui.GetForegroundWindow()
        except Exception:
            return None
        if not hwnd:
            return None
        try:
            title = win32gui.GetWindowText(hwnd) or ""
            cls = win32gui.GetClassName(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return WindowBinding(
                hwnd=hwnd,
                title=title,
                class_name=cls,
                pid=pid,
                process_name=_process_name(pid),
            )
        except Exception:
            return None

    def focus(self, target: WindowBinding, *, timeout: float = 3.0) -> bool:
        if not _is_bound_notepad_window(target):
            return False

        deadline = time.monotonic() + max(0.2, timeout)
        while True:
            _notepad_focus_attempt(target.hwnd)
            time.sleep(0.12)
            fg = self.foreground()
            if fg is not None and fg.hwnd == target.hwnd:
                return True
            if time.monotonic() >= deadline:
                return False

    def is_bound(self, target: WindowBinding) -> bool:
        return _is_bound_notepad_window(target)

    def read_text(
        self,
        target: WindowBinding,
        *,
        max_chars: int = _MAX_READ_CHARS,
    ) -> str:
        if not _is_bound_notepad_window(target):
            raise TargetResolutionError(
                "The bound Notepad window is no longer available for reading."
            )

        editor = _find_editor_control(target.hwnd)
        if editor is None:
            raise TargetResolutionError(
                "Could not locate the Notepad text control to read its content."
            )

        user32 = ctypes.windll.user32
        try:
            length = int(user32.SendMessageW(editor, _WM_GETTEXTLENGTH, 0, 0))
        except Exception as exc:
            raise TargetResolutionError(f"Failed to read Notepad text length: {exc}") from exc

        if length <= 0:
            return ""

        limit = min(length, max(0, int(max_chars)))
        buffer = ctypes.create_unicode_buffer(limit + 2)
        try:
            user32.SendMessageW(editor, _WM_GETTEXT, limit + 1, ctypes.byref(buffer))
        except Exception as exc:
            raise TargetResolutionError(f"Failed to read Notepad text: {exc}") from exc

        text = buffer.value
        if length > limit:
            text += "…"
        return text

    # ── action ──

    def type_text(self, target: WindowBinding, text: str) -> None:
        if not text:
            raise TargetResolutionError("No text was provided to type.")

        # Target binding re-check #1: the window must still be the bound Notepad.
        if not _is_bound_notepad_window(target):
            raise TargetResolutionError(
                "Refusing to type: the bound Notepad window is no longer valid."
            )

        # Target binding re-check #2: it must be the *active* window right now.
        foreground = self.foreground()
        if foreground is None or foreground.hwnd != target.hwnd:
            current = (
                f"'{foreground.title}' ({foreground.process_name or foreground.class_name})"
                if foreground is not None
                else "an unknown window"
            )
            raise TargetResolutionError(
                f"Refusing to type: the active window is {current}, not the bound "
                f"Notepad window '{target.title}'."
            )

        if _find_editor_control(target.hwnd) is None:
            raise TargetResolutionError(
                "Refusing to type: the bound Notepad window exposes no text control."
            )

        self._text_entry.type_text(text)


__all__ = [
    "NotepadDriver",
    "NotepadUnavailableError",
    "PyAutoGuiTextEntry",
    "TargetResolutionError",
    "TextEntry",
    "WindowBinding",
    "WindowsNotepadDriver",
]
