"""Notepad cross-application adapter (compatibility surface).

This module used to hold the Notepad-specific implementation. That logic is now
generic: window primitives live in :mod:`tools.desktop._win32`, observation
mechanisms in :mod:`tools.desktop.observation`, the application registry in
:mod:`tools.desktop.apps`, and the launch/bind/focus/type/observe driver in
:mod:`tools.desktop.driver`.

What remains here is the thin Notepad adapter surface: a driver bound to the
Notepad adapter, preserving the API the desktop tools and tests already use.
Notepad itself is described declaratively in
``tools.desktop.apps._notepad`` — no Notepad-specific execution logic exists.
"""

from __future__ import annotations

from ._win32 import (
    PyAutoGuiTextEntry,
    TargetResolutionError,
    TextEntry,
    WindowBinding,
    window_of,
)
from .apps import APP_NOTEPAD, default_application_registry
from .driver import ApplicationUnavailableError, BoundApplicationDriver, CrossAppDriver

_MAX_READ_CHARS = 32768
_DEFAULT_EDITOR_TIMEOUT = 5.0

NotepadUnavailableError = ApplicationUnavailableError
"""Backwards-compatible name: "Notepad could not be used at all"."""

__all__ = [
    "NotepadDriver",
    "NotepadUnavailableError",
    "PyAutoGuiTextEntry",
    "TargetResolutionError",
    "TextEntry",
    "WindowBinding",
    "WindowsNotepadDriver",
    "read_notepad_text",
]


class WindowsNotepadDriver(BoundApplicationDriver):
    """Notepad-facing view of the generic cross-application driver."""

    def __init__(self, *, text_entry: TextEntry | None = None) -> None:
        super().__init__(
            driver=CrossAppDriver(
                registry=default_application_registry(),
                text_entry=text_entry or PyAutoGuiTextEntry(),
            ),
            app_id=APP_NOTEPAD,
        )


class NotepadDriver:
    """Protocol-compatible alias for the Notepad-facing driver surface.

    Kept as a distinct name so tools/tests can depend on the Notepad surface
    without importing the generic driver.
    """

    def is_available(self) -> tuple[bool, str]: ...  # pragma: no cover - typing only

    def find_windows(self) -> list[WindowBinding]: ...  # pragma: no cover

    def launch(self, *, timeout: float = 12.0) -> WindowBinding: ...  # pragma: no cover

    def focus(self, target: WindowBinding, *, timeout: float = 2.0) -> bool: ...  # pragma: no cover

    def foreground(self) -> WindowBinding | None: ...  # pragma: no cover

    def type_text(self, target: WindowBinding, text: str) -> None: ...  # pragma: no cover

    def read_text(self, target: WindowBinding, *, max_chars: int = _MAX_READ_CHARS) -> str: ...  # pragma: no cover

    def is_bound(self, target: WindowBinding) -> bool: ...  # pragma: no cover

    def wait_for_editor(self, target: WindowBinding, *, timeout: float = _DEFAULT_EDITOR_TIMEOUT) -> bool: ...  # pragma: no cover


def read_notepad_text(hwnd: int | None = None) -> str:
    """Read the text of a bound Notepad window (raises on failure)."""
    driver = WindowsNotepadDriver()
    target = None
    if hwnd is not None:
        candidate = window_of(int(hwnd))
        target = candidate.identified_as(APP_NOTEPAD) if candidate else None
    if target is None:
        windows = driver.find_windows()
        if not windows:
            raise TargetResolutionError("No Notepad window is open.")
        target = windows[0]
    return driver.read_text(target)
