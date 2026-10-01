"""Notepad tool wrappers (compatibility surface).

Each Notepad tool is the corresponding generic cross-application tool with the
application preset to Notepad. No Notepad-specific interaction logic exists here
or anywhere else — see :mod:`tools.desktop.cross_app_tools` for the shared
implementation and :mod:`tools.desktop.apps` for the Notepad adapter record.
"""

from __future__ import annotations

from typing import Any

from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .apps import APP_NOTEPAD, ApplicationRegistry, default_application_registry
from .cross_app_tools import (
    InspectApplicationsHandler,
    LaunchApplicationHandler,
    ReadApplicationTextHandler,
    TypeTextInApplicationHandler,
)
from .driver import BoundApplicationDriver
from .notepad import NotepadDriver, WindowsNotepadDriver
from .types import DESKTOP_OPERATIONS, DesktopAction


def _socket_and_registry(
    driver: NotepadDriver | None,
) -> tuple[Any, ApplicationRegistry, Any]:
    """Resolve the driver, its registry, and the availability authority.

    A :class:`~tools.desktop.notepad.WindowsNotepadDriver` wraps the generic
    driver, which owns availability checks. A socket-style driver (custom
    integration or test double) is authoritative for its own availability, so an
    injected driver is never silently bypassed.
    """
    if driver is None:
        return WindowsNotepadDriver(), default_application_registry(), None

    inner = getattr(driver, "driver", None)
    if inner is not None and hasattr(inner, "registry"):
        registry = inner.registry
        return (
            inner,
            registry if isinstance(registry, ApplicationRegistry) else default_application_registry(),
            None,
        )

    if isinstance(driver, BoundApplicationDriver):
        return driver.driver, driver.driver.registry, None

    registry = getattr(driver, "registry", None)
    return (
        driver,
        registry if isinstance(registry, ApplicationRegistry) else default_application_registry(),
        driver,
    )


class LaunchNotepadHandler(LaunchApplicationHandler):
    """Launch Notepad and bind the new window as the target."""

    def __init__(self, driver: NotepadDriver | None = None) -> None:
        socket, registry, availability = _socket_and_registry(driver)
        super().__init__(
            socket,
            registry=registry,
            fallback_app_id=APP_NOTEPAD,
            socket=availability,
        )


class TypeTextInNotepadHandler(TypeTextInApplicationHandler):
    """Type text into an explicitly bound Notepad window."""

    def __init__(self, driver: NotepadDriver | None = None) -> None:
        socket, registry, availability = _socket_and_registry(driver)
        super().__init__(
            socket,
            registry=registry,
            fallback_app_id=APP_NOTEPAD,
            socket=availability,
        )


class ReadNotepadTextHandler(ReadApplicationTextHandler):
    """Read text back out of a bound Notepad window."""

    def __init__(self, driver: NotepadDriver | None = None) -> None:
        socket, registry, availability = _socket_and_registry(driver)
        super().__init__(
            socket,
            registry=registry,
            fallback_app_id=APP_NOTEPAD,
            socket=availability,
        )


class LaunchNotepadTool(BaseTool):
    def __init__(self, handler: LaunchNotepadHandler | None = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_NOTEPAD]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
            handler=handler or LaunchNotepadHandler(),
        )


class TypeTextInNotepadTool(BaseTool):
    def __init__(self, handler: TypeTextInNotepadHandler | None = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_NOTEPAD]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
            handler=handler or TypeTextInNotepadHandler(),
        )


class ReadNotepadTextTool(BaseTool):
    def __init__(self, handler: ReadNotepadTextHandler | None = None) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_NOTEPAD_TEXT]
        super().__init__(
            tool=Tool(name=defn.name, description=defn.description, metadata=defn.to_metadata()),
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
