"""Observation probes for cross-application adapters.

An observation probe is the smallest reliable mechanism that answers
"did the requested outcome actually occur?" for one application. Probes are
declarative: an application adapter names the probe it supports, and the generic
cross-app driver runs it. That keeps per-application code thin and stops the
observation surface from growing into a computer-vision system.

Supported probe families:

``TextControlProbe``
    Reads a Win32 text control (e.g. Notepad's editor) through ``WM_GETTEXT``.
    Read-only, cross-process safe, no input synthesised. This is the preferred
    probe whenever the application is a classic text control.

``ClipboardCopyProbe``
    For applications that expose their state through a native "copy" command
    (Windows Calculator copies its display on Ctrl+C, for example). The current
    clipboard is snapshotted, a sentinel is planted, the application's own
    shortcut is invoked, and the clipboard is compared against the sentinel — so
    a failed copy is detected rather than misread as a stale value. The original
    clipboard text is restored afterwards.

``WindowStateProbe``
    Confirms the target window itself (process alive, title, class) without
    needing any application-specific readback.

Every probe reports one of three outcomes: ``verified``, ``failed`` (an
observation was obtained and did not match), or ``unverified`` (no reliable
observation could be obtained — reported honestly, never as success).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ._win32 import (
    TargetResolutionError,
    WindowBinding,
    find_child_by_chain,
    find_child_by_classes,
    focus_window,
    foreground_window,
    press_key,
    read_control_text,
    window_of,
)

# ── observation outcomes ───────────────────────────────────────────────────
OBSERVATION_VERIFIED = "verified"
OBSERVATION_FAILED = "failed"
OBSERVATION_UNVERIFIED = "unverified"

_VK_CONTROL = 0x11
_VK_C = 0x43


@dataclass(frozen=True, slots=True)
class ObservationResult:
    """Outcome of an observation probe."""

    status: str
    detail: str
    content: str | None = None
    source: str = ""

    @property
    def is_verified(self) -> bool:
        return self.status == OBSERVATION_VERIFIED

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "observation_status": self.status,
            "observation_source": self.source,
            "observation_detail": self.detail,
        }
        if self.content is not None:
            metadata["observed_content"] = self.content
        return metadata


def _missed(expected: str, observed: str, source: str) -> ObservationResult:
    shown = observed.strip()
    if len(shown) > 200:
        shown = shown[:200] + "…"
    return ObservationResult(
        status=OBSERVATION_FAILED,
        detail=(
            f"observed content from {source} was {shown!r}, which does not contain "
            f"{expected!r}"
        ),
        content=observed,
        source=source,
    )


class ObservationProbe:
    """Contract for a small, application-declared observation mechanism."""

    label: str = "observation"

    def available(self, target: WindowBinding) -> tuple[bool, str]:
        """Whether this probe can observe the given window at all."""
        return True, ""

    def observe(self, target: WindowBinding) -> tuple[str | None, str]:
        """Read the current observable content. Returns (content, error)."""
        raise NotImplementedError

    def confirm(self, target: WindowBinding, expected: str) -> ObservationResult:
        content, error = self.observe(target)
        if content is None:
            return ObservationResult(
                status=OBSERVATION_UNVERIFIED,
                detail=error or "observation could not be obtained",
                source=self.label,
            )
        if expected.casefold() in content.casefold():
            return ObservationResult(
                status=OBSERVATION_VERIFIED,
                detail=f"observed content from {self.label} contains {expected!r}",
                content=content,
                source=self.label,
            )
        return _missed(expected, content, self.label)


@dataclass(frozen=True, slots=True)
class TextControlProbe(ObservationProbe):
    """Read the text held by a classic Win32 text control inside the window."""

    # Either a fixed child-class chain (preferred: unambiguous) …
    class_chain: tuple[str, ...] = ()
    # … or a set of acceptable control classes found anywhere in the window.
    control_classes: tuple[str, ...] = ()

    label: str = "window text control"

    def _control(self, target: WindowBinding) -> int | None:
        if self.class_chain:
            return find_child_by_chain(target.hwnd, self.class_chain)
        if self.control_classes:
            return find_child_by_classes(target.hwnd, self.control_classes)
        return None

    def available(self, target: WindowBinding) -> tuple[bool, str]:
        try:
            control = self._control(target)
        except Exception as exc:
            return False, f"could not inspect the window's controls: {exc}"
        if control is None:
            return False, "the window exposes no readable text control"
        return True, ""

    def observe(self, target: WindowBinding) -> tuple[str | None, str]:
        try:
            control = self._control(target)
        except Exception as exc:
            return None, f"could not inspect the window's controls: {exc}"
        if control is None:
            return None, "the window exposes no readable text control"
        try:
            return read_control_text(control), ""
        except TargetResolutionError as exc:
            return None, str(exc)


@dataclass(frozen=True, slots=True)
class ClipboardCopyProbe(ObservationProbe):
    """Observe state an application exposes through its native copy command."""

    shortcut_vk: int = _VK_C
    ctrl: bool = True
    settle_seconds: float = 0.5

    label: str = "application copy command"

    def observe(self, target: WindowBinding) -> tuple[str | None, str]:
        try:
            import pyperclip
        except ImportError as exc:  # pragma: no cover - dependency is declared
            return None, f"clipboard access is unavailable: {exc}"

        try:
            original = pyperclip.paste() or ""
        except Exception as exc:
            return None, f"could not read the clipboard: {exc}"

        sentinel = f"mamba-observe-{uuid4().hex}"
        try:
            pyperclip.copy(sentinel)
        except Exception as exc:
            return None, f"could not prepare the clipboard for observation: {exc}"

        try:
            # The application must be the active window for its shortcut to land.
            if not focus_window(target.hwnd, timeout=1.5):
                return None, "the target window could not be made active to read its state"
            press_key(self.shortcut_vk, ctrl=self.ctrl)
            time.sleep(self.settle_seconds)
            try:
                copied = pyperclip.paste() or ""
            except Exception as exc:
                return None, f"could not read the clipboard after copying: {exc}"
            if copied == sentinel:
                # The application never touched the clipboard, so nothing was
                # observed. Reporting the stale value would be a false reading.
                return None, "the application did not respond to its copy command"
            return copied, ""
        finally:
            try:
                pyperclip.copy(original)
            except Exception:
                pass


@dataclass(frozen=True, slots=True)
class WindowStateProbe(ObservationProbe):
    """Confirm the bound window itself (no application-specific readback)."""

    label: str = "window state"

    def observe(self, target: WindowBinding) -> tuple[str | None, str]:
        live = window_of(target.hwnd)
        if live is None:
            return None, "the bound window is no longer open"
        return f"{live.title} [{live.class_name}]", ""


__all__ = [
    "ClipboardCopyProbe",
    "OBSERVATION_FAILED",
    "OBSERVATION_UNVERIFIED",
    "OBSERVATION_VERIFIED",
    "ObservationProbe",
    "ObservationResult",
    "TextControlProbe",
    "WindowStateProbe",
]
