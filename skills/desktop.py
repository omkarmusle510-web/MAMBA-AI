"""Desktop skills and task handler for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import webbrowser

from core.context import ExecutionContext
from tasks.types import TaskInput, TaskOutput
from tools.desktop.clipboard import (
    ClearClipboardTool,
    ReadClipboardTool,
    WriteClipboardTool,
)
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
    notepad_operation_for,
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

# ── Cross-app (Notepad) intents ──
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
    }
)
_READ_NOTEPAD_TEXT_INTENTS = frozenset(
    {"read_notepad_text", "notepad_text", "get_notepad_text"}
)

_NOTEPAD_INTENTS = _LAUNCH_NOTEPAD_INTENTS | _TYPE_TEXT_INTENTS | _READ_NOTEPAD_TEXT_INTENTS



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


# ── Cross-application (Notepad) skills ──────────────────────────────────────


class LaunchNotepadSkill(BaseSkill):
    """Launch Notepad and bind the new window as the target for later steps."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
        driver: NotepadDriver | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.LAUNCH_NOTEPAD]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or LaunchNotepadTool(handler=LaunchNotepadHandler(driver=driver))
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


class TypeTextInNotepadSkill(BaseSkill):
    """Type text into an explicitly bound Notepad window."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
        driver: NotepadDriver | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.TYPE_TEXT_IN_NOTEPAD]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or TypeTextInNotepadTool(
            handler=TypeTextInNotepadHandler(driver=driver)
        )
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        tool_input = ToolInput(
            arguments=dict(meta),
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


class ReadNotepadTextSkill(BaseSkill):
    """Read the current text content of a bound Notepad window."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        executor: ToolExecutor | None = None,
        driver: NotepadDriver | None = None,
    ) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_NOTEPAD_TEXT]
        super().__init__(
            Skill(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            )
        )
        self._tool = tool or ReadNotepadTextTool(
            handler=ReadNotepadTextHandler(driver=driver)
        )
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        meta = input.task_input.step_metadata
        tool_input = ToolInput(
            arguments=dict(meta),
            metadata=dict(meta),
        )
        tool_output = self._executor.execute(self._tool, tool_input)
        return SkillOutput(
            content=str(tool_output.result or tool_output.error or ""),
            success=tool_output.success,
            metadata=tool_output.metadata,
        )


@dataclass(slots=True)
class DesktopTaskHandler:
    """Dispatches desktop/window/clipboard task inputs to concrete skills."""

    get_foreground_window_skill: GetForegroundWindowSkill | None = None
    get_window_title_skill: GetWindowTitleSkill | None = None
    find_window_skill: FindWindowSkill | None = None
    focus_window_skill: FocusWindowSkill | None = None
    close_window_skill: CloseWindowSkill | None = None
    read_clipboard_skill: ReadClipboardSkill | None = None
    write_clipboard_skill: WriteClipboardSkill | None = None
    clear_clipboard_skill: ClearClipboardSkill | None = None
    open_url_skill: OpenURLSkill | None = None
    launch_notepad_skill: LaunchNotepadSkill | None = None
    type_text_in_notepad_skill: TypeTextInNotepadSkill | None = None
    read_notepad_text_skill: ReadNotepadTextSkill | None = None
    notepad_driver: NotepadDriver | None = None

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

        notepad_driver = self.notepad_driver
        notepad_needed = (
            self.launch_notepad_skill is None
            or self.type_text_in_notepad_skill is None
            or self.read_notepad_text_skill is None
        )
        if notepad_driver is None and notepad_needed:
            notepad_driver = WindowsNotepadDriver()
        if self.launch_notepad_skill is None:
            self.launch_notepad_skill = LaunchNotepadSkill(driver=notepad_driver)
        if self.type_text_in_notepad_skill is None:
            self.type_text_in_notepad_skill = TypeTextInNotepadSkill(driver=notepad_driver)
        if self.read_notepad_text_skill is None:
            self.read_notepad_text_skill = ReadNotepadTextSkill(driver=notepad_driver)

    def get_metadata(self, task_input: TaskInput) -> dict[str, Any]:
        """Return authoritative capability security metadata for this intent."""
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        notepad_meta = notepad_operation_for(intent)
        if notepad_meta is not None:
            return DESKTOP_OPERATIONS[notepad_meta].to_metadata()

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

        if intent in _LAUNCH_NOTEPAD_INTENTS:
            assert self.launch_notepad_skill is not None
            return self.launch_notepad_skill.run(skill_input).to_task_output()

        if intent in _TYPE_TEXT_INTENTS:
            assert self.type_text_in_notepad_skill is not None
            return self.type_text_in_notepad_skill.run(skill_input).to_task_output()

        if intent in _READ_NOTEPAD_TEXT_INTENTS:
            assert self.read_notepad_text_skill is not None
            return self.read_notepad_text_skill.run(skill_input).to_task_output()

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

