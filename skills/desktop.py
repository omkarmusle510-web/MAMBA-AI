"""Desktop skills and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import webbrowser

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.desktop.apps import APP_NOTEPAD, ApplicationRegistry, default_application_registry
from tools.desktop.clipboard import (
    ClearClipboardTool,
    ReadClipboardTool,
    WriteClipboardTool,
)
from tools.desktop.cross_app_tools import (
    InspectApplicationsHandler,
    InspectApplicationsTool,
    LaunchApplicationHandler,
    LaunchApplicationTool,
    ReadApplicationTextHandler,
    ReadApplicationTextTool,
    TypeTextInApplicationHandler,
    TypeTextInApplicationTool,
)
from tools.desktop.driver import BoundApplicationDriver, CrossAppDriver
from tools.desktop.notepad import (
    NotepadDriver,
    WindowsNotepadDriver,
)
from tools.desktop.notepad_tools import (
    LaunchNotepadHandler,
    LaunchNotepadTool,
    ReadNotepadTextHandler,
    ReadNotepadTextTool,
    TypeTextInNotepadHandler,
    TypeTextInNotepadTool,
)
from tools.desktop.types import (
    DESKTOP_OPERATIONS,
    DesktopAction,
    cross_app_action_for,
)
from tools.desktop.window import (
    CloseWindowTool,
    FindWindowTool,
    FocusWindowTool,
    GetForegroundWindowTool,
    GetWindowTitleTool,
)
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_FOREGROUND_INTENTS = frozenset(
    {"get_foreground_window", "foreground_window", "active_window", "get_active_window"}
)
_WINDOW_TITLE_INTENTS = frozenset({"get_window_title", "window_title"})
_FIND_WINDOW_INTENTS = frozenset({"find_window", "search_window", "find_windows"})
_FOCUS_WINDOW_INTENTS = frozenset(
    {"focus_window", "switch_window", "activate_window", "focus"}
)
_CLOSE_WINDOW_INTENTS = frozenset(
    {"close_window", "terminate_window", "kill_window", "destroy_window"}
)
_READ_CLIPBOARD_INTENTS = frozenset(
    {"read_clipboard", "get_clipboard", "clipboard_read", "paste"}
)
_WRITE_CLIPBOARD_INTENTS = frozenset(
    {"write_clipboard", "set_clipboard", "copy_to_clipboard", "clipboard_write", "copy"}
)
_CLEAR_CLIPBOARD_INTENTS = frozenset(
    {"clear_clipboard", "empty_clipboard", "clipboard_clear"}
)
_OPEN_URL_INTENTS = frozenset(
    {"open_url", "launch_url", "browse", "open_browser"}
)

# ── Cross-application intents ──
_LAUNCH_APP_INTENTS = frozenset(
    {
        "launch_application",
        "open_application",
        "start_application",
        "activate_application",
    }
)
_LAUNCH_NOTEPAD_INTENTS = frozenset(
    {"launch_notepad", "open_notepad", "start_notepad"}
)
_TYPE_TEXT_INTENTS = frozenset(
    {
        "type_text",
        "type_text_in_notepad",
        "type_in_notepad",
        "type_into_notepad",
        "write_in_notepad",
        "enter_text",
        "type_text_in_application",
        "write_in_application",
    }
)
_READ_APP_TEXT_INTENTS = frozenset(
    {
        "read_notepad_text",
        "notepad_text",
        "get_notepad_text",
        "read_application_text",
        "application_text",
        "read_app_text",
    }
)
_INSPECT_APPS_INTENTS = frozenset(
    {
        "inspect_applications",
        "list_applications",
        "supported_applications",
        "find_application",
    }
)

_CROSS_APP_INTENTS = (
    _LAUNCH_APP_INTENTS
    | _LAUNCH_NOTEPAD_INTENTS
    | _TYPE_TEXT_INTENTS
    | _READ_APP_TEXT_INTENTS
    | _INSPECT_APPS_INTENTS
)




class OpenURLSkill(BaseSkill):
    """Skill for safely launching a URL in the user's default browser."""

    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.OPEN_URL]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        url = meta.get("url") or meta.get("link") or meta.get("target") or ""
        if not url and isinstance(meta.get("arguments"), dict):
            url = meta["arguments"].get("url", "")
        url_str = str(url).strip()
        if not url_str:
            return SkillOutput(
                content="Missing required argument: 'url'",
                success=False,
                metadata={"error": "missing_url"},
            )
        if not url_str.startswith(("http://", "https://")):
            url_str = "https://" + url_str

        try:
            opened = webbrowser.open(url_str)
            return SkillOutput(
                content=f"Opened '{url_str}' in default browser.",
                success=True,
                metadata={"url": url_str, "opened": opened},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"Failed to open URL '{url_str}': {exc}",
                success=False,
                metadata={"url": url_str, "error": str(exc)},
            )



class GetForegroundWindowSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.GET_FOREGROUND_WINDOW]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or GetForegroundWindowTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        tool_input = ToolInput(
            arguments=dict(input.task_input.step_metadata),
            metadata=dict(input.task_input.step_metadata),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class GetWindowTitleSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.GET_WINDOW_TITLE]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or GetWindowTitleTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        hwnd = meta.get("hwnd")
        tool_input = ToolInput(
            arguments={"hwnd": hwnd} if hwnd is not None else {},
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class FindWindowSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.FIND_WINDOW]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or FindWindowTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        query = meta.get("query") or meta.get("title") or ""
        tool_input = ToolInput(
            arguments={"query": query},
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class FocusWindowSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.FOCUS_WINDOW]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or FocusWindowTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        args: dict[str, Any] = {}
        if "hwnd" in meta:
            args["hwnd"] = meta["hwnd"]
        if "query" in meta or "title" in meta:
            args["query"] = meta.get("query") or meta.get("title")
        tool_input = ToolInput(
            arguments=args,
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class CloseWindowSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.CLOSE_WINDOW]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or CloseWindowTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        args: dict[str, Any] = {}
        if "hwnd" in meta:
            args["hwnd"] = meta["hwnd"]
        if "query" in meta or "title" in meta:
            args["query"] = meta.get("query") or meta.get("title")
        tool_input = ToolInput(
            arguments=args,
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class ReadClipboardSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_CLIPBOARD]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or ReadClipboardTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        tool_input = ToolInput(
            arguments=dict(input.task_input.step_metadata),
            metadata=dict(input.task_input.step_metadata),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class WriteClipboardSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.WRITE_CLIPBOARD]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or WriteClipboardTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        text = meta.get("text") or meta.get("content") or meta.get("value") or ""
        tool_input = ToolInput(
            arguments={"text": text},
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class ClearClipboardSkill(BaseSkill):
    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.CLEAR_CLIPBOARD]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or ClearClipboardTool()
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        tool_input = ToolInput(
            arguments=dict(input.task_input.step_metadata),
            metadata=dict(input.task_input.step_metadata),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


def _notepad_only_registry(registry: ApplicationRegistry | None) -> ApplicationRegistry:
    """A registry advertising only the Notepad adapter.

    Notepad-flavoured intents (``type_text_in_notepad``, ``read_notepad_text``…)
    are scoped to Notepad: naming a different application makes resolution fail
    with a clear "unsupported application" error rather than acting elsewhere.
    """
    source = registry or default_application_registry()
    notepad = source.get(APP_NOTEPAD)
    if notepad is None:  # pragma: no cover - the default registry always has it
        return source
    return ApplicationRegistry((notepad,))


def _cross_app_driver_for(
    driver: CrossAppDriver | None,
    registry: ApplicationRegistry | None,
) -> CrossAppDriver:
    if driver is not None:
        return driver
    return CrossAppDriver(registry=registry or default_application_registry())


class _CrossAppSkill(BaseSkill):
    """Shared plumbing for cross-application skills.

    The skill layer stays thin: it forwards the step's operational metadata to
    the authoritative tool for the intent, and passes the tool's observation
    through unchanged (including the target-binding and observation metadata that
    the Brain's verification step relies on).
    """

    def __init__(self, defn, tool: BaseTool, executor: ToolExecutor | None = None) -> None:
        super().__init__(
            Skill(name=defn.name, description=defn.description, metadata=defn.to_metadata())
        )
        self._tool = tool
        self._executor = executor or StandardToolExecutor()

    @property
    def tool_handler(self) -> Any:
        """The tool handler behind this skill, for inspection and wiring checks."""
        return getattr(self._tool, "_handler", None)

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = dict(input.task_input.step_metadata)
        tool_output = self._executor.execute(
            self._tool,
            ToolInput(arguments=dict(meta), metadata=dict(meta)),
        )
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class LaunchApplicationSkill(_CrossAppSkill):
    """Launch a supported application and bind the window that appears."""

    def __init__(
        self,
        *,
        driver: CrossAppDriver | None = None,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_APPLICATION]
        super().__init__(
            defn,
            tool
            or LaunchApplicationTool(
                handler=LaunchApplicationHandler(
                    _cross_app_driver_for(driver, registry),
                    registry=registry,
                    default_app_id=default_app_id,
                )
            ),
            executor,
        )


class TypeTextInApplicationSkill(_CrossAppSkill):
    """Type text into an explicitly bound, verified-active application window."""

    def __init__(
        self,
        *,
        driver: CrossAppDriver | None = None,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_APPLICATION]
        super().__init__(
            defn,
            tool
            or TypeTextInApplicationTool(
                handler=TypeTextInApplicationHandler(
                    _cross_app_driver_for(driver, registry),
                    registry=registry,
                    default_app_id=default_app_id,
                )
            ),
            executor,
        )


class ReadApplicationTextSkill(_CrossAppSkill):
    """Read an application's currently observable content."""

    def __init__(
        self,
        *,
        driver: CrossAppDriver | None = None,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_APPLICATION_TEXT]
        super().__init__(
            defn,
            tool
            or ReadApplicationTextTool(
                handler=ReadApplicationTextHandler(
                    _cross_app_driver_for(driver, registry),
                    registry=registry,
                    default_app_id=default_app_id,
                )
            ),
            executor,
        )


class InspectApplicationsSkill(_CrossAppSkill):
    """Report which applications Mamba supports and which are open."""

    def __init__(
        self,
        *,
        driver: CrossAppDriver | None = None,
        registry: ApplicationRegistry | None = None,
        default_app_id: str | None = None,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.INSPECT_APPLICATIONS]
        super().__init__(
            defn,
            tool
            or InspectApplicationsTool(
                handler=InspectApplicationsHandler(_cross_app_driver_for(driver, registry))
            ),
            executor,
        )


# Notepad-flavoured skills: the generic skill with the application fixed.
class LaunchNotepadSkill(LaunchApplicationSkill):
    """Launch Notepad and bind the new window as the target."""

    def __init__(self, registry: ApplicationRegistry | None = None, **kwargs: Any) -> None:
        super().__init__(
            registry=registry or _notepad_only_registry(None),
            default_app_id=APP_NOTEPAD,
            **kwargs,
        )


class TypeTextInNotepadSkill(TypeTextInApplicationSkill):
    """Type text into an explicitly bound Notepad window."""

    def __init__(self, registry: ApplicationRegistry | None = None, **kwargs: Any) -> None:
        super().__init__(
            registry=registry or _notepad_only_registry(None),
            default_app_id=APP_NOTEPAD,
            **kwargs,
        )


class ReadNotepadTextSkill(ReadApplicationTextSkill):
    """Read text back out of a bound Notepad window."""

    def __init__(self, registry: ApplicationRegistry | None = None, **kwargs: Any) -> None:
        super().__init__(
            registry=registry or _notepad_only_registry(None),
            default_app_id=APP_NOTEPAD,
            **kwargs,
        )


@dataclass(slots=True)
class DesktopTaskHandler:
    """Dispatches desktop/window/clipboard/cross-app task inputs to concrete skills."""

    get_foreground_window_skill: GetForegroundWindowSkill | None = None
    get_window_title_skill: GetWindowTitleSkill | None = None
    find_window_skill: FindWindowSkill | None = None
    focus_window_skill: FocusWindowSkill | None = None
    close_window_skill: CloseWindowSkill | None = None
    read_clipboard_skill: ReadClipboardSkill | None = None
    write_clipboard_skill: WriteClipboardSkill | None = None
    clear_clipboard_skill: ClearClipboardSkill | None = None
    open_url_skill: OpenURLSkill | None = None
    launch_application_skill: BaseSkill | None = None
    type_text_in_application_skill: BaseSkill | None = None
    read_application_text_skill: BaseSkill | None = None
    inspect_applications_skill: BaseSkill | None = None
    launch_notepad_skill: BaseSkill | None = None
    type_text_in_notepad_skill: BaseSkill | None = None
    read_notepad_text_skill: BaseSkill | None = None
    cross_app_driver: CrossAppDriver | None = None
    notepad_driver: NotepadDriver | None = None
    application_registry: ApplicationRegistry | None = None
    notepad_registry: ApplicationRegistry | None = None
    """Registry whose supported-applications list is the Notepad adapter only.

    Used for Notepad-flavoured intents, so ``"notepad"`` is the default
    application while an explicitly named *different* application is still
    rejected as unsupported rather than silently acted on.
    """

    def __post_init__(self) -> None:
        if self.get_foreground_window_skill is None:
            self.get_foreground_window_skill = GetForegroundWindowSkill()
        if self.get_window_title_skill is None:
            self.get_window_title_skill = GetWindowTitleSkill()
        if self.find_window_skill is None:
            self.find_window_skill = FindWindowSkill()
        if self.focus_window_skill is None:
            self.focus_window_skill = FocusWindowSkill()
        if self.close_window_skill is None:
            self.close_window_skill = CloseWindowSkill()
        if self.read_clipboard_skill is None:
            self.read_clipboard_skill = ReadClipboardSkill()
        if self.write_clipboard_skill is None:
            self.write_clipboard_skill = WriteClipboardSkill()
        if self.clear_clipboard_skill is None:
            self.clear_clipboard_skill = ClearClipboardSkill()
        if self.open_url_skill is None:
            self.open_url_skill = OpenURLSkill()

        cross_app, notepad = self._cross_app_drivers()
        full_registry = self.application_registry or default_application_registry()
        notepad_registry = self.notepad_registry or _notepad_only_registry(full_registry)

        # Notepad-flavoured intents: the application is fixed to Notepad.
        if self.launch_notepad_skill is None:
            self.launch_notepad_skill = LaunchNotepadSkill(
                driver=notepad, registry=notepad_registry
            )
        if self.type_text_in_notepad_skill is None:
            self.type_text_in_notepad_skill = TypeTextInNotepadSkill(
                driver=notepad, registry=notepad_registry
            )
        if self.read_notepad_text_skill is None:
            self.read_notepad_text_skill = ReadNotepadTextSkill(
                driver=notepad, registry=notepad_registry
            )

        # Generic intents: resolve whichever supported application is named.
        if self.launch_application_skill is None:
            self.launch_application_skill = LaunchApplicationSkill(
                driver=cross_app, registry=full_registry
            )
        if self.type_text_in_application_skill is None:
            self.type_text_in_application_skill = TypeTextInApplicationSkill(
                driver=cross_app, registry=full_registry
            )
        if self.read_application_text_skill is None:
            self.read_application_text_skill = ReadApplicationTextSkill(
                driver=cross_app, registry=full_registry
            )
        if self.inspect_applications_skill is None:
            self.inspect_applications_skill = InspectApplicationsSkill(
                driver=cross_app,
                registry=full_registry,
            )

    def _cross_app_drivers(self) -> tuple[Any, NotepadDriver | None]:
        """Resolve the generic driver and the Notepad-facing driver.

        One driver instance serves every application; the Notepad surface is the
        same driver scoped to the Notepad adapter. A socket-style driver (custom
        integration or test double) is accepted as-is and used for every skill,
        so an injected driver is never silently bypassed.
        """
        socket_driver: Any = None
        if self.notepad_driver is not None:
            inner = getattr(self.notepad_driver, "driver", None)
            if isinstance(inner, CrossAppDriver):
                socket_driver = inner
            else:
                socket_driver = self.notepad_driver
        generic = self.cross_app_driver

        if generic is not None and socket_driver is not None:
            return generic, self.notepad_driver
        if generic is not None:
            return generic, BoundApplicationDriver(generic, APP_NOTEPAD)
        if socket_driver is not None:
            return socket_driver, self.notepad_driver or socket_driver
        created = CrossAppDriver(
            registry=self.application_registry or default_application_registry()
        )
        return created, BoundApplicationDriver(created, APP_NOTEPAD)

    @property
    def registry(self) -> ApplicationRegistry:
        """The application registry this handler acts on.

        A Notepad-scoped wiring (only ``notepad_driver`` was injected) reports
        just the Notepad adapter, so the execution layer sees exactly what the
        capability can target. A generic wiring reports every supported adapter.
        """
        if self.application_registry is not None:
            return self.application_registry
        if self.cross_app_driver is None and self.notepad_driver is not None:
            return self.notepad_registry or _notepad_only_registry(None)
        return default_application_registry()

    @staticmethod
    def _implies_notepad(meta: dict[str, Any]) -> bool:
        """Whether a step's metadata already points at Notepad.

        Generic intents (``type_text``) are routed to the Notepad-scoped skill
        when the step names Notepad (or names nothing while carrying a window
        handle), so an unspecified application keeps its unambiguous Notepad
        default while an explicitly named other application takes the generic
        path — and is then resolved or rejected on its own merits.
        """
        raw = str(
            meta.get("app_id")
            or meta.get("app")
            or meta.get("application")
            or meta.get("target_app")
            or ""
        ).strip().lower()
        if raw in ("", "notepad", "notepad.exe"):
            return True
        return False

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for this intent."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        cross_app_meta = cross_app_action_for(intent)
        if cross_app_meta is not None:
            return DESKTOP_OPERATIONS[cross_app_meta].to_metadata()

        if intent in _OPEN_URL_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.OPEN_URL].to_metadata()
        if intent in _CLOSE_WINDOW_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.CLOSE_WINDOW].to_metadata()
        if intent in _FOCUS_WINDOW_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.FOCUS_WINDOW].to_metadata()
        if intent in _WRITE_CLIPBOARD_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.WRITE_CLIPBOARD].to_metadata()
        if intent in _CLEAR_CLIPBOARD_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.CLEAR_CLIPBOARD].to_metadata()
        if intent in _READ_CLIPBOARD_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.READ_CLIPBOARD].to_metadata()
        if intent in _FIND_WINDOW_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.FIND_WINDOW].to_metadata()
        if intent in _WINDOW_TITLE_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.GET_WINDOW_TITLE].to_metadata()
        if intent in _FOREGROUND_INTENTS:
            return DESKTOP_OPERATIONS[DesktopAction.GET_FOREGROUND_WINDOW].to_metadata()

        return {"action": intent, "risk_level": "low"}

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        skill_input = SkillInput.from_task(task_input, context)
        step_meta = dict(task_input.step_metadata)

        # ── cross-application intents ──
        # Notepad-flavoured intents use the Notepad-scoped skills (the application
        # is fixed, and any other application is rejected as unsupported); generic
        # intents use the application named in the step's metadata.
        if intent in _INSPECT_APPS_INTENTS:
            assert self.inspect_applications_skill is not None
            return self.inspect_applications_skill.run(skill_input).to_task_output()

        if intent in _LAUNCH_APP_INTENTS:
            assert self.launch_application_skill is not None
            return self.launch_application_skill.run(skill_input).to_task_output()

        if intent in _LAUNCH_NOTEPAD_INTENTS:
            assert self.launch_notepad_skill is not None
            return self.launch_notepad_skill.run(skill_input).to_task_output()

        if intent in _TYPE_TEXT_INTENTS:
            if self._implies_notepad(step_meta):
                assert self.type_text_in_notepad_skill is not None
                return self.type_text_in_notepad_skill.run(skill_input).to_task_output()
            assert self.type_text_in_application_skill is not None
            return self.type_text_in_application_skill.run(skill_input).to_task_output()

        if intent in _READ_APP_TEXT_INTENTS:
            if self._implies_notepad(step_meta):
                assert self.read_notepad_text_skill is not None
                return self.read_notepad_text_skill.run(skill_input).to_task_output()
            assert self.read_application_text_skill is not None
            return self.read_application_text_skill.run(skill_input).to_task_output()

        if intent in _OPEN_URL_INTENTS:
            assert self.open_url_skill is not None
            return self.open_url_skill.run(skill_input).to_task_output()

        if intent in _FOREGROUND_INTENTS:
            assert self.get_foreground_window_skill is not None
            return self.get_foreground_window_skill.run(skill_input).to_task_output()

        if intent in _WINDOW_TITLE_INTENTS:
            assert self.get_window_title_skill is not None
            return self.get_window_title_skill.run(skill_input).to_task_output()

        if intent in _FIND_WINDOW_INTENTS:
            assert self.find_window_skill is not None
            return self.find_window_skill.run(skill_input).to_task_output()

        if intent in _FOCUS_WINDOW_INTENTS:
            assert self.focus_window_skill is not None
            return self.focus_window_skill.run(skill_input).to_task_output()

        if intent in _CLOSE_WINDOW_INTENTS:
            assert self.close_window_skill is not None
            return self.close_window_skill.run(skill_input).to_task_output()

        if intent in _READ_CLIPBOARD_INTENTS:
            assert self.read_clipboard_skill is not None
            return self.read_clipboard_skill.run(skill_input).to_task_output()

        if intent in _WRITE_CLIPBOARD_INTENTS:
            assert self.write_clipboard_skill is not None
            return self.write_clipboard_skill.run(skill_input).to_task_output()

        if intent in _CLEAR_CLIPBOARD_INTENTS:
            assert self.clear_clipboard_skill is not None
            return self.clear_clipboard_skill.run(skill_input).to_task_output()

        return TaskOutput(
            content=f"unsupported desktop capability intent: '{task_input.intent}'",
            success=False,
            metadata={"error": "unsupported_capability"},
        )

