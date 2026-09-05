"""Filesystem skills capability for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.context import ExecutionContext
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tools.errors import ToolError
from tools.filesystem.tool import ListDirectoryTool, ReadFileTool, WriteFileTool
from tools.filesystem.types import FILESYSTEM_OPERATIONS, FilesystemAction
from tools.protocols import ToolExecutor
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill, SkillTaskHandler
from .types import SkillInput, SkillOutput

_LIST_DIR_SUPPORTED_INTENTS = frozenset({"list_directory", "list_dir"})
_READ_FILE_SUPPORTED_INTENTS = frozenset({"read_file"})
_WRITE_FILE_SUPPORTED_INTENTS = frozenset({"write_file"})


class ListDirectorySkill(BaseSkill):
    """Skill for listing directory contents using ListDirectoryTool."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        root_dir: str | Path | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.LIST_DIRECTORY]
        skill = Skill(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        super().__init__(skill)
        self._tool = tool or ListDirectoryTool(root_dir=root_dir)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        """Execute the list_directory capability."""
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _LIST_DIR_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )

        path = self._resolve_path_arg(input.task_input.step_metadata)

        tool_input = ToolInput(
            arguments={"path": path},
            metadata=dict(input.task_input.step_metadata),
        )

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={"error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"filesystem tool execution failed: {exc}",
                success=False,
                metadata={"error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "list_directory failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        result = tool_output.result or {}
        entries = result.get("entries", [])
        resolved_path = result.get("path", path)
        content_lines = [f"Directory listing for '{resolved_path}':"]
        if entries:
            content_lines.extend(f"- {name}" for name in entries)
        else:
            content_lines.append("(empty directory)")

        return SkillOutput(
            content="\n".join(content_lines),
            success=True,
            metadata={
                **dict(tool_output.metadata),
                "path": resolved_path,
                "entries": entries,
                "count": len(entries),
            },
        )

    def _resolve_path_arg(self, metadata: dict[str, Any]) -> str:
        val = metadata.get("path") or metadata.get("directory")
        if isinstance(val, str) and val.strip():
            return val.strip()
        return "."


class ReadFileSkill(BaseSkill):
    """Skill for reading file contents using ReadFileTool."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        root_dir: str | Path | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.READ_FILE]
        skill = Skill(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        super().__init__(skill)
        self._tool = tool or ReadFileTool(root_dir=root_dir)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        """Execute the read_file capability."""
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _READ_FILE_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )

        path = self._resolve_path_arg(input.task_input.step_metadata)
        if not path:
            return SkillOutput(
                content="Missing required argument: 'path'",
                success=False,
                metadata={"error": "missing_path"},
            )

        arguments: dict[str, Any] = {"path": path}
        if "encoding" in input.task_input.step_metadata:
            arguments["encoding"] = input.task_input.step_metadata["encoding"]

        tool_input = ToolInput(
            arguments=arguments,
            metadata=dict(input.task_input.step_metadata),
        )

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={"error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"filesystem tool execution failed: {exc}",
                success=False,
                metadata={"error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "read_file failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        result = tool_output.result or {}
        content = result.get("content", "")
        resolved_path = result.get("path", path)

        return SkillOutput(
            content=content,
            success=True,
            metadata={
                **dict(tool_output.metadata),
                "path": resolved_path,
                "bytes_read": len(content.encode("utf-8")),
            },
        )

    def _resolve_path_arg(self, metadata: dict[str, Any]) -> str | None:
        val = metadata.get("path") or metadata.get("file") or metadata.get("filename")
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None


class WriteFileSkill(BaseSkill):
    """Skill for writing text files using WriteFileTool."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        root_dir: str | Path | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.WRITE_FILE]
        skill = Skill(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        super().__init__(skill)
        self._tool = tool or WriteFileTool(root_dir=root_dir)
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        """Execute the write_file capability."""
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _WRITE_FILE_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )

        path = self._resolve_path_arg(input.task_input.step_metadata)
        if not path:
            return SkillOutput(
                content="Missing required argument: 'path'",
                success=False,
                metadata={"error": "missing_path"},
            )

        if "content" not in input.task_input.step_metadata and "text" not in input.task_input.step_metadata:
            return SkillOutput(
                content="Missing required argument: 'content'",
                success=False,
                metadata={"error": "missing_content"},
            )

        content = input.task_input.step_metadata.get("content")
        if content is None:
            content = input.task_input.step_metadata.get("text")

        if not isinstance(content, str):
            return SkillOutput(
                content=f"content must be a string, got {type(content).__name__}",
                success=False,
                metadata={"error": "invalid_content_type"},
            )

        arguments: dict[str, Any] = {
            "path": path,
            "content": content,
        }
        if "encoding" in input.task_input.step_metadata:
            arguments["encoding"] = input.task_input.step_metadata["encoding"]

        tool_input = ToolInput(
            arguments=arguments,
            metadata=dict(input.task_input.step_metadata),
        )

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={"error": type(exc).__name__},
            )
        except Exception as exc:
            return SkillOutput(
                content=f"filesystem tool execution failed: {exc}",
                success=False,
                metadata={"error": type(exc).__name__},
            )

        if not tool_output.success:
            return SkillOutput(
                content=tool_output.error or "write_file failed",
                success=False,
                metadata=dict(tool_output.metadata),
            )

        result = tool_output.result or {}
        resolved_path = result.get("path", path)
        bytes_written = result.get("bytes_written", len(content.encode("utf-8")))

        return SkillOutput(
            content=f"Successfully wrote {bytes_written} bytes to '{resolved_path}'",
            success=True,
            metadata={
                **dict(tool_output.metadata),
                "path": resolved_path,
                "bytes_written": bytes_written,
            },
        )

    def _resolve_path_arg(self, metadata: dict[str, Any]) -> str | None:
        val = metadata.get("path") or metadata.get("file") or metadata.get("filename")
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None


FilesystemSkill = ListDirectorySkill


@dataclass(slots=True)
class FilesystemTaskHandler:
    """Dispatches filesystem task inputs to appropriate filesystem skills."""

    list_directory_skill: ListDirectorySkill
    read_file_skill: ReadFileSkill
    write_file_skill: WriteFileSkill

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        if intent in _LIST_DIR_SUPPORTED_INTENTS:
            skill_input = SkillInput.from_task(task_input, context)
            return self.list_directory_skill.run(skill_input).to_task_output()
        elif intent in _READ_FILE_SUPPORTED_INTENTS:
            skill_input = SkillInput.from_task(task_input, context)
            return self.read_file_skill.run(skill_input).to_task_output()
        elif intent in _WRITE_FILE_SUPPORTED_INTENTS:
            skill_input = SkillInput.from_task(task_input, context)
            return self.write_file_skill.run(skill_input).to_task_output()
        else:
            return TaskOutput(
                content=f"unsupported capability intent: '{task_input.intent}'",
                success=False,
                metadata={"error": "unsupported_capability"},
            )


def create_filesystem_task_executor(
    tool: BaseTool | None = None,
    *,
    root_dir: str | Path | None = None,
    executor: ToolExecutor | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to filesystem capabilities."""
    if isinstance(tool, WriteFileTool):
        skill = WriteFileSkill(tool=tool, root_dir=root_dir, executor=executor)
        return TaskExecutor(handler=SkillTaskHandler(handler=skill))
    if isinstance(tool, ReadFileTool):
        skill = ReadFileSkill(tool=tool, root_dir=root_dir, executor=executor)
        return TaskExecutor(handler=SkillTaskHandler(handler=skill))
    if isinstance(tool, ListDirectoryTool):
        skill = ListDirectorySkill(tool=tool, root_dir=root_dir, executor=executor)
        return TaskExecutor(handler=SkillTaskHandler(handler=skill))

    handler = FilesystemTaskHandler(
        list_directory_skill=ListDirectorySkill(root_dir=root_dir, executor=executor),
        read_file_skill=ReadFileSkill(root_dir=root_dir, executor=executor),
        write_file_skill=WriteFileSkill(root_dir=root_dir, executor=executor),
    )
    return TaskExecutor(handler=handler)
