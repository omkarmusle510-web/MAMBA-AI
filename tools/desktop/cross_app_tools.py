"""Generic cross-application tools for Mamba.

One set of tools serves every supported application. The application is resolved
through the adapter registry, the exact target window is bound before any action,
and observation goes through the adapter's declared probe. There are no
per-application tools here — the Notepad tools are thin wrappers over
:class:`LaunchApplicationHandler` / :class:`TypeTextInApplicationHandler` /
:class:`ReadApplicationTextHandler` with the application preset.
"""

from __future__ import annotations

from typing import Any

from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from ._win32 import TargetResolutionError, WindowBinding
from .apps import ApplicationAdapter, ApplicationRegistry, default_application_registry
from .driver import ApplicationUnavailableError, CrossAppDriver
from .observation import ObservationResult
from .types import DESKTOP_OPERATIONS, DesktopAction

_DEFAULT_FOCUS_TIMEOUT = 2.0
_DEFAULT_READY_TIMEOUT = 5.0

_APP_KEYS = ("app_id", "app", "application", "target_app", "program", "app_name")


def resolve_adapter(
    meta: dict[str, Any],
    registry: ApplicationRegistry,
    *,
    default_app_id: str | None = None,
    fallback_app_id: str | None = None,
) -> tuple[ApplicationAdapter | None, str]:
    """Resolve the declared application from step metadata.

    Precedence:

    1. an application named in the step (``app`` / ``app_id`` / …) — resolved
       against the registry, and rejected when unsupported;
    2. ``fallback_app_id`` / ``default_app_id`` — the application an
       application-scoped tool was preset with.

    The fallback never overrides an explicitly named application, so a step
    naming a different or unsupported application is rejected rather than
    silently retargeted, and an unnamed application is never guessed from
    whichever window happens to be focused.
    """
    fallback = fallback_app_id or default_app_id
    raw: Any = None
    for key in _APP_KEYS:
        if meta.get(key):
            raw = meta[key]
            break
    if raw is None:
        raw = fallback
    if raw is None:
        supported = ", ".join(a.app_id for a in registry.all())
        return None, (
            "No target application was specified. State which application to act on "
            f"(supported: {supported})."
        )
    adapter = registry.resolve(str(raw))
    if adapter is None:
        supported = ", ".join(a.app_id for a in registry.all())
        return None, (
            f"'{raw}' is not a supported application; Mamba can only interact with: "
            f"{supported}."
        )
    return adapter, ""


def adapter_metadata(
    adapter: ApplicationAdapter,
    target: WindowBinding | None = None,
) -> dict[str, Any]:
    """Target-binding metadata attached to every cross-app observation."""
    metadata: dict[str, Any] = {
        "app": adapter.display_name,
        "app_id": adapter.app_id,
        "target_bound": target is not None,
    }
    if target is not None:
        metadata.update(
            {
                "hwnd": target.hwnd,
                "target_title": target.title,
                "target_pid": target.pid,
                "target_process": target.process_name,
                "target_class": target.class_name,
            }
        )
    return metadata


def _binding_from_metadata(meta: dict[str, Any]) -> WindowBinding | None:
    """Rebuild a full window binding from step metadata, when one was carried."""
    hwnd = meta.get("hwnd")
    pid = meta.get("target_pid")
    if hwnd is None or pid is None:
        return None
    try:
        return WindowBinding(
            hwnd=int(hwnd),
            title=str(meta.get("target_title") or ""),
            class_name=str(meta.get("target_class") or ""),
            pid=int(pid),
            process_name=str(meta.get("target_process") or ""),
            app_id=str(meta.get("app_id") or ""),
        )
    except (TypeError, ValueError):
        return None


def _socket_windows(driver: Any, adapter: ApplicationAdapter) -> list[WindowBinding]:
    """Open windows of *this* application, through whichever driver API exists.

    Application-scoped lookups come first so a multi-application driver is never
    asked for "all windows" and mistaken for a match.
    """
    finder = getattr(driver, "find", None)
    if callable(finder):
        try:
            return list(finder(adapter.app_id))
        except Exception:
            pass
    for name in ("find_windows", "describe_windows"):
        method = getattr(driver, name, None)
        if callable(method):
            try:
                return list(method())
            except Exception:
                continue
    return []


def _live_window(driver: Any, hwnd: int) -> WindowBinding | None:
    """Find a live window with this handle across all supported applications."""
    direct = getattr(driver, "_find_by_handle", None)
    if callable(direct):
        try:
            return direct(hwnd)
        except Exception:
            return None
    window_of = getattr(driver, "window_of", None)
    if callable(window_of):
        try:
            return window_of(hwnd)
        except Exception:
            return None
    return None


def bind_target(
    driver: Any,
    adapter: ApplicationAdapter,
    meta: dict[str, Any],
    *,
    timeout: float = 6.0,
) -> tuple[WindowBinding | None, str]:
    """Bind the exact target window for an application action.

    An explicit handle is honoured only if it verifies as the requested
    application; a carried binding is re-verified; otherwise the application's
    own open window is used. Nothing is inferred from the foreground window.
    """
    hwnd = meta.get("hwnd")
    if hwnd is not None:
        try:
            raw_hwnd = int(hwnd)
        except (TypeError, ValueError):
            return None, f"Invalid target window handle: {hwnd!r}."
        windows = _socket_windows(driver, adapter)
        for candidate in windows:
            if candidate.hwnd == raw_hwnd:
                return candidate.identified_as(adapter.app_id), ""
        # Distinguish "that window belongs to another supported application" from
        # "unknown window": both are refused, each with an accurate reason.
        if _live_window(driver, raw_hwnd) is not None:
            return None, (
                f"Window {raw_hwnd} is not the requested {adapter.display_name} "
                "window; refusing to act on the wrong target."
            )
        return None, (
            f"The specified window (HWND {raw_hwnd}) is not an open "
            f"{adapter.display_name} window; refusing to act on an unverified target."
        )

    carried = _binding_from_metadata(meta)
    if carried is not None:
        marked = carried.identified_as(adapter.app_id)
        if driver.is_bound(marked):
            return marked, ""
        return None, (
            f"The previously bound {adapter.display_name} window is no longer valid; "
            "refusing to act on a stale target."
        )

    windows = _socket_windows(driver, adapter)
    if windows:
        return windows[0].identified_as(adapter.app_id), ""
    return None, (
        f"No open {adapter.display_name} window was found. Launch it first, or repeat "
        "the request after it is open."
    )


def _availability(driver: Any, socket: Any, app_id: str) -> tuple[bool, str]:
    """Report application availability through whichever driver is authoritative.

    A socket-style driver supplied by the caller (custom integration or test
    double) is authoritative; otherwise the generic driver's own check is used.
    """
    for candidate in (socket, driver):
        if candidate is None:
            continue
        checker = getattr(candidate, "is_available", None)
        if not callable(checker):
            continue
        try:
            return checker(app_id)
        except TypeError:
            try:
                return checker()
            except Exception:
                continue
        except Exception:
            continue
    return False, f"availability of '{app_id}' could not be determined"


def _launch(driver: Any, adapter: ApplicationAdapter, options: dict[str, Any]) -> WindowBinding:
    """Launch through whichever driver API is available."""
    launcher = getattr(driver, "launch_with_options", None)
    if callable(launcher):
        return launcher(adapter.app_id, options=options)
    return driver.launch()


def _wait_until_ready(driver: Any, target: WindowBinding, *, timeout: float) -> bool:
    """Wait for a launched window to be actionable, when the driver can tell."""
    for name in ("wait_until_ready", "wait_for_editor"):
        waiter = getattr(driver, name, None)
        if callable(waiter):
            try:
                return bool(waiter(target, timeout=timeout))
            except Exception:
                return False
    return True


def _read_content(driver: Any, target: WindowBinding, adapter: ApplicationAdapter) -> tuple[str | None, str]:
    """Read observable content through the driver's read operation."""
    reader = getattr(driver, "read_text", None)
    if not callable(reader):
        return None, f"{adapter.display_name} exposes no readable content"
    try:
        return reader(target), ""
    except TargetResolutionError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"could not read {adapter.display_name} content: {exc}"


def _observe(driver: Any, target: WindowBinding, adapter: ApplicationAdapter, expected: str) -> ObservationResult:
    """Observe an outcome through the adapter's probe, when the driver supports it."""
    observer = getattr(driver, "observe", None)
    if callable(observer):
        try:
            return observer(target, expected)
        except Exception as exc:
            return ObservationResult(
                status="unverified",
                detail=f"observation failed: {exc}",
                source="error",
            )
    return adapter.observe(target, expected)


class LaunchApplicationHandler:
    """Launch a supported application and bind the window that appears."""

    def __init__(
        self,
        driver: Any = None,
        *,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        fallback_app_id: str | None = None,
        socket: Any = None,
    ) -> None:
        if driver is None:
            self._driver: Any = CrossAppDriver(
                registry=registry or default_application_registry()
            )
            self._registry = self._driver.registry
        else:
            self._driver = driver
            self._registry = getattr(driver, "registry", None) or registry or default_application_registry()
        self._default_app_id = default_app_id
        self._fallback_app_id = fallback_app_id
        # The caller-supplied driver is authoritative for availability when the
        # generic driver only delegates to it (custom integration / test double).
        self._socket = socket

    @property
    def registry(self) -> ApplicationRegistry:
        """The application registry this handler resolves targets with.

        Exposed so the execution layer can resolve application names against the
        same authoritative registry the capability uses.
        """
        return self._registry

    @property
    def driver(self) -> Any:
        return self._driver

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_APPLICATION].to_metadata()
        meta = {**dict(input.metadata), **dict(input.arguments)}

        adapter, reason = resolve_adapter(
            meta,
            self._registry,
            default_app_id=self._default_app_id,
            fallback_app_id=self._fallback_app_id,
        )
        if adapter is None:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={**auth, "error": "application_not_resolved"},
            )

        available, unavailable_reason = _availability(self._driver, self._socket, adapter.app_id)
        if not available:
            return ToolOutput(
                success=False,
                error=unavailable_reason,
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "available": False,
                    "error": "application_unavailable",
                },
            )

        options = {
            key: meta[key]
            for key in ("folder", "path", "arguments", "args")
            if meta.get(key) is not None
        }
        try:
            target = _launch(self._driver, adapter, options)
        except ApplicationUnavailableError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "error": "launch_failed",
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to launch {adapter.display_name}: {exc}",
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "error": "launch_failed",
                },
            )

        target = target.identified_as(adapter.app_id)
        ready = _wait_until_ready(self._driver, target, timeout=_DEFAULT_READY_TIMEOUT)
        focused = False
        try:
            focused = self._driver.focus(target, timeout=_DEFAULT_FOCUS_TIMEOUT)
        except Exception:
            focused = False

        return ToolOutput(
            success=True,
            result=(
                f"{adapter.display_name} launched and bound to window "
                f"'{target.title}' (HWND {target.hwnd}, PID {target.pid})"
                + ("" if focused else "; focus was not confirmed")
                + "."
            ),
            metadata={
                **auth,
                **adapter_metadata(adapter, target),
                "launched": True,
                "focused": focused,
                "ready": ready,
            },
        )


class TypeTextInApplicationHandler:
    """Type text into an explicitly bound, verified-active application window."""

    def __init__(
        self,
        driver: Any = None,
        *,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        fallback_app_id: str | None = None,
        socket: Any = None,
    ) -> None:
        if driver is None:
            self._driver: Any = CrossAppDriver(
                registry=registry or default_application_registry()
            )
            self._registry = self._driver.registry
        else:
            self._driver = driver
            self._registry = getattr(driver, "registry", None) or registry or default_application_registry()
        self._default_app_id = default_app_id
        self._fallback_app_id = fallback_app_id
        # The caller-supplied driver is authoritative for availability when the
        # generic driver only delegates to it (custom integration / test double).
        self._socket = socket

    @property
    def registry(self) -> ApplicationRegistry:
        """The application registry this handler resolves targets with."""
        return self._registry

    @property
    def driver(self) -> Any:
        return self._driver

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_APPLICATION].to_metadata()
        meta = {**dict(input.metadata), **dict(input.arguments)}

        text = meta.get("text")
        if text is None:
            text = meta.get("content")
        if not isinstance(text, str) or not text.strip():
            return ToolOutput(
                success=False,
                error="Missing required argument: 'text'",
                metadata={**auth, "error": "missing_text"},
            )

        adapter, reason = resolve_adapter(
            meta,
            self._registry,
            default_app_id=self._default_app_id,
            fallback_app_id=self._fallback_app_id,
        )
        if adapter is None:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={**auth, "error": "application_not_resolved"},
            )

        if not adapter.supports_text_input:
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to type: typing text into {adapter.display_name} is not "
                    "a supported action."
                ),
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "error": "text_input_unsupported",
                },
            )

        available, unavailable_reason = _availability(self._driver, self._socket, adapter.app_id)
        if not available:
            return ToolOutput(
                success=False,
                error=unavailable_reason,
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "available": False,
                    "error": "application_unavailable",
                },
            )

        target, problem = bind_target(self._driver, adapter, meta)
        if target is None:
            return ToolOutput(
                success=False,
                error=problem,
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "error": "target_not_resolved",
                    "target_bound": False,
                },
            )

        if not self._driver.is_bound(target):
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to type: the bound {adapter.display_name} window is no "
                    "longer valid."
                ),
                metadata={
                    **auth,
                    **adapter_metadata(adapter, target),
                    "error": "target_stale",
                    "target_bound": False,
                },
            )

        if not _wait_until_ready(self._driver, target, timeout=_DEFAULT_READY_TIMEOUT):
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to type: the bound {adapter.display_name} window is not "
                    "ready to receive input."
                ),
                metadata={
                    **auth,
                    **adapter_metadata(adapter, target),
                    "error": "target_not_ready",
                },
            )

        if not self._driver.focus(target, timeout=_DEFAULT_FOCUS_TIMEOUT):
            active = None
            try:
                active = self._driver.foreground()
            except Exception:
                active = None
            current = (
                f"'{active.title}' ({active.process_name or 'unknown'})"
                if active is not None
                else "an unknown window"
            )
            return ToolOutput(
                success=False,
                error=(
                    f"Refusing to type: the bound {adapter.display_name} window could not "
                    f"be made the active window (active window is {current})."
                ),
                metadata={
                    **auth,
                    **adapter_metadata(adapter, target),
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
                metadata={
                    **auth,
                    **adapter_metadata(adapter, target),
                    "error": "typing_refused",
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Failed to type into {adapter.display_name}: {exc}",
                metadata={
                    **auth,
                    **adapter_metadata(adapter, target),
                    "error": "typing_failed",
                },
            )

        return ToolOutput(
            success=True,
            result=(
                f"Typed {len(text)} character(s) into the bound {adapter.display_name} "
                f"window '{target.title}' (HWND {target.hwnd})."
            ),
            metadata={
                **auth,
                **adapter_metadata(adapter, target),
                "focused": True,
                "typed": True,
                "text": text,
                "text_length": len(text),
            },
        )


class ReadApplicationTextHandler:
    """Read an application's currently observable content."""

    def __init__(
        self,
        driver: Any = None,
        *,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        fallback_app_id: str | None = None,
        socket: Any = None,
    ) -> None:
        if driver is None:
            self._driver: Any = CrossAppDriver(
                registry=registry or default_application_registry()
            )
            self._registry = self._driver.registry
        else:
            self._driver = driver
            self._registry = getattr(driver, "registry", None) or registry or default_application_registry()
        self._default_app_id = default_app_id
        self._fallback_app_id = fallback_app_id
        # The caller-supplied driver is authoritative for availability when the
        # generic driver only delegates to it (custom integration / test double).
        self._socket = socket

    @property
    def registry(self) -> ApplicationRegistry:
        """The application registry this handler resolves targets with."""
        return self._registry

    @property
    def driver(self) -> Any:
        return self._driver

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.READ_APPLICATION_TEXT].to_metadata()
        meta = {**dict(input.metadata), **dict(input.arguments)}

        adapter, reason = resolve_adapter(
            meta,
            self._registry,
            default_app_id=self._default_app_id,
            fallback_app_id=self._fallback_app_id,
        )
        if adapter is None:
            return ToolOutput(
                success=False,
                error=reason,
                metadata={**auth, "error": "application_not_resolved"},
            )

        target, problem = bind_target(self._driver, adapter, meta)
        if target is None:
            return ToolOutput(
                success=False,
                error=problem,
                metadata={
                    **auth,
                    "app": adapter.display_name,
                    "app_id": adapter.app_id,
                    "error": "target_not_resolved",
                    "target_bound": False,
                },
            )

        content, error = _read_content(self._driver, target, adapter)
        if content is None:
            return ToolOutput(
                success=False,
                error=error,
                metadata={
                    **auth,
                    **adapter_metadata(adapter, target),
                    "error": "not_observable",
                },
            )

        return ToolOutput(
            success=True,
            result=content,
            metadata={
                **auth,
                **adapter_metadata(adapter, target),
                "text": content,
                "text_length": len(content),
                "window_title": target.title,
            },
        )


class InspectApplicationsHandler:
    """Report supported applications and the open windows that identify as them."""

    def __init__(self, driver: Any = None) -> None:
        self._driver: Any = driver or CrossAppDriver()

    @property
    def driver(self) -> Any:
        return self._driver

    def run(self, input: ToolInput) -> ToolOutput:
        auth = DESKTOP_OPERATIONS[DesktopAction.INSPECT_APPLICATIONS].to_metadata()
        try:
            described = self._driver.discover()
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Could not inspect applications: {exc}",
                metadata={**auth, "error": "inspection_failed"},
            )

        lines = []
        for entry in described:
            state = "installed" if entry["installed"] else "not installed"
            titles = ", ".join(w["title"] for w in entry["open_windows"]) or "no open windows"
            lines.append(f"- {entry['name']} ({state}): {titles}")

        return ToolOutput(
            success=True,
            result="Supported applications:\n" + "\n".join(lines),
            metadata={**auth, "applications": described},
        )


# ── tool wrappers ──────────────────────────────────────────────────────────


class LaunchApplicationTool(BaseTool):
    def __init__(self, handler: Any = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_APPLICATION]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
            handler=handler or LaunchApplicationHandler(),
        )


class TypeTextInApplicationTool(BaseTool):
    def __init__(self, handler: Any = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_APPLICATION]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
            handler=handler or TypeTextInApplicationHandler(),
        )


class ReadApplicationTextTool(BaseTool):
    def __init__(self, handler: Any = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_APPLICATION_TEXT]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
            handler=handler or ReadApplicationTextHandler(),
        )


class InspectApplicationsTool(BaseTool):
    def __init__(self, handler: Any = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.INSPECT_APPLICATIONS]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
            handler=handler or InspectApplicationsHandler(),
        )


__all__ = [
    "InspectApplicationsHandler",
    "InspectApplicationsTool",
    "LaunchApplicationHandler",
    "LaunchApplicationTool",
    "ReadApplicationTextHandler",
    "ReadApplicationTextTool",
    "TypeTextInApplicationHandler",
    "TypeTextInApplicationTool",
    "adapter_metadata",
    "bind_target",
    "resolve_adapter",
]
