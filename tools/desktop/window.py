"""Window awareness and control tools for Mamba."""

from __future__ import annotations

import sys
import time
from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from ._win32 import window_of
from .errors import WindowError, WindowNotFoundError
from .types import DESKTOP_OPERATIONS, DesktopAction, WindowInfo


def _is_windows() -> bool:
    return sys.platform == "win32"


class GetForegroundWindowHandler:
    """Handler for identifying the current foreground window."""

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Window operations are only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            import win32gui
        except ImportError:
            return ToolOutput(
                success=False,
                error="pywin32 (win32gui) is not installed.",
                metadata={"available": False},
            )

        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return ToolOutput(
                    success=True,
                    result="No foreground window currently active.",
                    metadata={"hwnd": 0, "title": "", "active": False},
                )

            title = win32gui.GetWindowText(hwnd) or ""
            return ToolOutput(
                success=True,
                result=f"Foreground window: '{title}' (HWND: {hwnd})",
                metadata={"hwnd": hwnd, "title": title, "active": True},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to inspect foreground window: {exc}",
                metadata={"error": str(exc)},
            )


class GetWindowTitleHandler:
    """Handler for retrieving a window's title."""

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Window operations are only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            import win32gui
        except ImportError:
            return ToolOutput(
                success=False,
                error="pywin32 (win32gui) is not installed.",
                metadata={"available": False},
            )

        hwnd_val = input.arguments.get("hwnd")
        try:
            if hwnd_val is None:
                hwnd = win32gui.GetForegroundWindow()
            else:
                hwnd = int(hwnd_val)

            if not hwnd:
                return ToolOutput(
                    success=False,
                    error="No valid window handle specified or active.",
                    metadata={"hwnd": 0},
                )

            title = win32gui.GetWindowText(hwnd) or ""
            return ToolOutput(
                success=True,
                result=title,
                metadata={"hwnd": hwnd, "title": title, "actual": title},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to get window title: {exc}",
                metadata={"error": str(exc)},
            )


class FindWindowHandler:
    """Handler for finding windows by title substring."""

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Window operations are only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            import win32gui
        except ImportError:
            return ToolOutput(
                success=False,
                error="pywin32 (win32gui) is not installed.",
                metadata={"available": False},
            )

        query = str(input.arguments.get("query") or "").strip()
        if not query:
            return ToolOutput(
                success=False,
                error="Search query must not be empty.",
                metadata={"matches": []},
            )

        matches: list[WindowInfo] = []

        def _enum_cb(hwnd: int, _: Any) -> bool:
            try:
                if win32gui.IsWindowVisible(hwnd):
                    text = win32gui.GetWindowText(hwnd)
                    if text and query.lower() in text.lower():
                        matches.append(WindowInfo(hwnd=hwnd, title=text, visible=True))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
            matches_data = [w.to_dict() for w in matches]
            if not matches:
                return ToolOutput(
                    success=True,
                    result=f"No visible windows found matching '{query}'.",
                    metadata={"query": query, "count": 0, "matches": []},
                )

            summary = f"Found {len(matches)} window(s) matching '{query}':\n" + "\n".join(
                f"- '{w.title}' (HWND: {w.hwnd})" for w in matches
            )
            return ToolOutput(
                success=True,
                result=summary,
                metadata={"query": query, "count": len(matches), "matches": matches_data},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Window search failed: {exc}",
                metadata={"error": str(exc)},
            )


class FocusWindowHandler:
    """Handler for focusing a window by HWND or title query."""

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Window operations are only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            import win32gui
        except ImportError:
            return ToolOutput(
                success=False,
                error="pywin32 (win32gui) is not installed.",
                metadata={"available": False},
            )

        hwnd_val = input.arguments.get("hwnd")
        query = str(input.arguments.get("query") or "").strip()

        target_hwnd: int | None = None
        target_title: str = ""

        if hwnd_val is not None:
            try:
                target_hwnd = int(hwnd_val)
                target_title = win32gui.GetWindowText(target_hwnd) or ""
            except Exception:
                target_hwnd = None

        if target_hwnd is None and query:
            # Find matching window
            candidates: list[tuple[int, str]] = []

            def _find_cb(h: int, _: Any) -> bool:
                try:
                    if win32gui.IsWindowVisible(h):
                        t = win32gui.GetWindowText(h)
                        if t and query.lower() in t.lower():
                            candidates.append((h, t))
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(_find_cb, None)
            if candidates:
                target_hwnd, target_title = candidates[0]

        if target_hwnd is None:
            return ToolOutput(
                success=False,
                error=f"Target window not found (query: '{query}', hwnd: {hwnd_val}).",
                metadata={"target_found": False},
            )

        try:
            win32gui.ShowWindow(target_hwnd, 9)  # SW_RESTORE
            time.sleep(0.05)
            win32gui.SetForegroundWindow(target_hwnd)
            return ToolOutput(
                success=True,
                result=f"Focused window '{target_title}' (HWND: {target_hwnd}).",
                metadata={
                    "target_found": True,
                    "hwnd": target_hwnd,
                    "title": target_title,
                    "actual": target_hwnd,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to focus window {target_hwnd}: {exc}",
                metadata={"target_found": True, "hwnd": target_hwnd, "error": str(exc)},
            )


class CloseWindowHandler:
    """Handler for requesting a window to close via WM_CLOSE.

    Closing is consequential — it can discard the user's unsaved work — so this
    handler never guesses a target and never substitutes a re-found or currently
    focused window. It acts only on the exact window recorded when it was bound:
    an explicit handle plus the owning process id (and window class / process
    name when recorded), revalidated against the live window immediately before
    the close request. Stale, replaced, or ambiguously identified targets are
    refused. Confirmation for this action comes from the existing permission
    policy (close_window is HIGH risk); this handler adds no second mechanism.
    """

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Window operations are only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            import win32con
            import win32gui
        except ImportError:
            return ToolOutput(
                success=False,
                error="pywin32 (win32gui/win32con) is not installed.",
                metadata={"available": False},
            )

        arguments = getattr(input, "arguments", None) or {}
        metadata = getattr(input, "metadata", None) or {}
        raw: dict[str, Any] = {}
        if isinstance(metadata, dict):
            raw.update(metadata)
        if isinstance(arguments, dict):
            raw.update(arguments)

        hwnd_val = raw.get("hwnd")
        query = str(raw.get("query") or raw.get("title") or "").strip()

        if hwnd_val is None:
            if query:
                return ToolOutput(
                    success=False,
                    error=(
                        f"Refusing to close a window by title search ('{query}'): closing "
                        "must target the exact window handle recorded when the window was "
                        "bound, so another window with a matching title can never be closed."
                    ),
                    metadata={
                        "target_found": False,
                        "close_requested": False,
                        "error": "explicit_handle_required",
                    },
                )
            return ToolOutput(
                success=False,
                error=(
                    "No target window handle was specified; refusing to close a window "
                    "without an explicit, recorded target."
                ),
                metadata={
                    "target_found": False,
                    "close_requested": False,
                    "error": "explicit_handle_required",
                },
            )

        try:
            target_hwnd = int(hwnd_val)
        except (TypeError, ValueError):
            return ToolOutput(
                success=False,
                error=f"Invalid target window handle: {hwnd_val!r}.",
                metadata={
                    "target_found": False,
                    "close_requested": False,
                    "error": "invalid_handle",
                },
            )

        recorded_pid = raw.get("target_pid")
        if recorded_pid is None:
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to close window {target_hwnd}: the recorded owning process "
                    "identity (target_pid) was not carried, so the window cannot be "
                    "revalidated as the intended target."
                ),
                metadata={
                    "target_found": False,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "error": "recorded_identity_required",
                },
            )
        try:
            recorded_pid = int(recorded_pid)
        except (TypeError, ValueError):
            return ToolOutput(
                success=False,
                error=f"Invalid recorded process id for window {target_hwnd}: {recorded_pid!r}.",
                metadata={
                    "target_found": False,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "error": "invalid_recorded_identity",
                },
            )

        # Revalidate the exact recorded identity against the live window before
        # acting: a stale handle, a replaced/reused window, or any identity
        # mismatch stops the close instead of retargeting.
        live = window_of(target_hwnd)
        if live is None:
            return ToolOutput(
                success=False,
                error=(
                    f"The target window (HWND {target_hwnd}) is no longer open; "
                    "nothing was closed."
                ),
                metadata={
                    "target_found": False,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "error": "target_gone",
                },
            )

        if live.pid != recorded_pid:
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to close window {target_hwnd}: it now belongs to a "
                    f"different process (recorded PID {recorded_pid}, live PID {live.pid}). "
                    "The window was replaced or its handle was reused; closing it could "
                    "close the wrong window."
                ),
                metadata={
                    "target_found": True,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "title": live.title,
                    "error": "target_replaced",
                },
            )

        recorded_class = raw.get("target_class")
        if recorded_class and str(recorded_class) != live.class_name:
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to close window {target_hwnd}: its window class changed "
                    f"(recorded '{recorded_class}', live '{live.class_name}'); the handle "
                    "no longer names the recorded window."
                ),
                metadata={
                    "target_found": True,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "title": live.title,
                    "error": "target_replaced",
                },
            )

        recorded_process = raw.get("target_process")
        if recorded_process and live.process_name and str(recorded_process) != live.process_name:
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to close window {target_hwnd}: its owning process changed "
                    f"(recorded '{recorded_process}', live '{live.process_name}')."
                ),
                metadata={
                    "target_found": True,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "title": live.title,
                    "error": "target_replaced",
                },
            )

        try:
            win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
            # Check whether window remains or closed
            time.sleep(0.1)
            still_open = bool(win32gui.IsWindow(target_hwnd))

            status_desc = "still open" if still_open else "closed"
            msg = (
                f"Close request sent to the recorded window '{live.title}' "
                f"(HWND {target_hwnd}, PID {live.pid}). Window is currently {status_desc}."
            )

            return ToolOutput(
                success=True,
                result=msg,
                metadata={
                    "target_found": True,
                    "close_requested": True,
                    "hwnd": target_hwnd,
                    "title": live.title,
                    "still_open": still_open,
                    "expected": False,
                    "actual": still_open,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to post WM_CLOSE to window {target_hwnd}: {exc}",
                metadata={
                    "target_found": True,
                    "close_requested": False,
                    "hwnd": target_hwnd,
                    "error": str(exc),
                },
            )


class GetForegroundWindowTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.GET_FOREGROUND_WINDOW]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=GetForegroundWindowHandler(),
        )


class GetWindowTitleTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.GET_WINDOW_TITLE]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=GetWindowTitleHandler(),
        )


class FindWindowTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.FIND_WINDOW]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=FindWindowHandler(),
        )


class FocusWindowTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.FOCUS_WINDOW]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=FocusWindowHandler(),
        )


class CloseWindowTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.CLOSE_WINDOW]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=CloseWindowHandler(),
        )

