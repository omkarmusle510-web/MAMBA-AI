"""Terminal skill capability for Mamba."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import sys
from typing import Any

from core.context import ExecutionContext
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tools.errors import ToolError
from tools.protocols import ToolExecutor
from tools.terminal.tool import TerminalTool
from tools.terminal.types import TERMINAL_TOOL_METADATA
from tools.tool import BaseTool, StandardToolExecutor
from tools.types import ToolInput

from .skill import BaseSkill, Skill
from .types import SkillInput, SkillOutput

_TERMINAL_SUPPORTED_INTENTS = frozenset(
    {
        "execute_command",
        "run_command",
        "terminal",
        "exec",
        "command",
    }
)


class TerminalSkill(BaseSkill):
    """Skill for controlled execution of external terminal commands."""

    def __init__(
        self,
        tool: BaseTool | None = None,
        *,
        root_dir: str | Path | None = None,
        default_timeout: float | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        skill = Skill(
            name="execute_command",
            description="Execute a controlled command with explicit arguments without a shell.",
            metadata=dict(TERMINAL_TOOL_METADATA),
        )
        super().__init__(skill)
        self._tool = tool or TerminalTool(
            root_dir=root_dir,
            default_timeout=default_timeout,
        )
        self._executor = executor or StandardToolExecutor()

    def execute(self, input: SkillInput) -> SkillOutput:
        """Execute the terminal command capability."""
        intent = (
            input.task_input.step_metadata.get("action")
            or input.task_input.intent
            or ""
        ).strip().lower()

        if intent not in _TERMINAL_SUPPORTED_INTENTS:
            return SkillOutput(
                content=f"unsupported capability intent: '{input.task_input.intent}'",
                success=False,
                metadata={
                    **TERMINAL_TOOL_METADATA,
                    "error": "unsupported_capability",
                },
            )

        extracted = self._extract_arguments(input.task_input.step_metadata)
        executable = extracted.get("executable")
        if not executable:
            return SkillOutput(
                content="Missing required argument: 'executable' or 'command'",
                success=False,
                metadata={
                    **TERMINAL_TOOL_METADATA,
                    "error": "missing_executable",
                },
            )

        tool_input = ToolInput(
            arguments=extracted,
            metadata=dict(input.task_input.step_metadata),
        )

        try:
            tool_output = self._executor.execute(self._tool, tool_input)
        except ToolError as exc:
            return SkillOutput(
                content=str(exc),
                success=False,
                metadata={
                    **TERMINAL_TOOL_METADATA,
                    "error": type(exc).__name__,
                },
            )
        except Exception as exc:
            return SkillOutput(
                content=f"terminal execution failed: {exc}",
                success=False,
                metadata={
                    **TERMINAL_TOOL_METADATA,
                    "error": type(exc).__name__,
                },
            )

        result = tool_output.result or {}
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        exit_code = result.get("exit_code")
        timed_out = result.get("timed_out", False)

        combined_metadata = {
            **dict(tool_output.metadata),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cwd": result.get("cwd"),
            "command": result.get("command"),
        }

        if not tool_output.success:
            content = (
                tool_output.error
                or stderr.strip()
                or stdout.strip()
                or f"Command '{executable}' failed with exit code {exit_code}"
            )
            return SkillOutput(
                content=content,
                success=False,
                metadata=combined_metadata,
            )

        content = (
            stdout.strip()
            if stdout.strip()
            else (stderr.strip() if stderr.strip() else f"Command '{executable}' executed successfully.")
        )
        return SkillOutput(
            content=content,
            success=True,
            metadata=combined_metadata,
        )

    def _extract_arguments(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Extract executable, args, cwd, and timeout from metadata or nested sub-dicts."""
        args_dict: dict[str, Any] = {}

        # First check nested dicts if present
        for sub_key in ("arguments", "args", "parameters", "params", "input"):
            sub = metadata.get(sub_key)
            if isinstance(sub, dict):
                args_dict.update(sub)

        # Top-level metadata overrides nested sub-dicts
        for k, v in metadata.items():
            if k not in ("arguments", "parameters", "params", "input"):
                args_dict[k] = v

        executable = args_dict.get("executable")
        cmd_args = args_dict.get("args")

        # Fallback if "command" or "cmd" passed
        if not executable:
            cmd_val = args_dict.get("command") or args_dict.get("cmd")
            if isinstance(cmd_val, (list, tuple)):
                if cmd_val:
                    executable = cmd_val[0]
                    if not cmd_args:
                        cmd_args = list(cmd_val[1:])
                    else:
                        cmd_args = list(cmd_val[1:]) + list(cmd_args)
            elif isinstance(cmd_val, str) and cmd_val.strip():
                executable = cmd_val.strip()

        # If executable is a string with arguments (e.g. "python -V") and args not specified
        if isinstance(executable, str) and " " in executable and not cmd_args:
            if not Path(executable).exists():
                try:
                    posix_mode = sys.platform != "win32"
                    parts = shlex.split(executable, posix=posix_mode)
                    if len(parts) > 1:
                        executable = parts[0]
                        cmd_args = parts[1:]
                except Exception:
                    pass

        # Normalize args if passed as "arguments" list
        if not cmd_args and isinstance(metadata.get("arguments"), (list, tuple)):
            cmd_args = metadata["arguments"]

        result: dict[str, Any] = {}
        if executable is not None:
            result["executable"] = executable
        if cmd_args is not None:
            result["args"] = tuple(cmd_args) if isinstance(cmd_args, (list, tuple)) else (cmd_args,)
        else:
            result["args"] = ()

        cwd = (
            args_dict.get("cwd")
            or args_dict.get("directory")
            or args_dict.get("dir")
            or args_dict.get("workdir")
        )
        if cwd is not None:
            result["cwd"] = cwd

        timeout = args_dict.get("timeout")
        if timeout is not None:
            result["timeout"] = timeout

        return result


@dataclass(slots=True)
class TerminalTaskHandler:
    """Dispatches terminal task inputs to TerminalSkill."""

    terminal_skill: TerminalSkill

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        intent = (
            task_input.step_metadata.get("action")
            or task_input.intent
            or ""
        ).strip().lower()

        if intent in _TERMINAL_SUPPORTED_INTENTS:
            skill_input = SkillInput.from_task(task_input, context)
            return self.terminal_skill.run(skill_input).to_task_output()
        else:
            return TaskOutput(
                content=f"unsupported capability intent: '{task_input.intent}'",
                success=False,
                metadata={
                    **TERMINAL_TOOL_METADATA,
                    "error": "unsupported_capability",
                },
            )


def create_terminal_task_executor(
    tool: BaseTool | None = None,
    *,
    root_dir: str | Path | None = None,
    default_timeout: float | None = None,
    executor: ToolExecutor | None = None,
) -> TaskExecutor:
    """Create a TaskExecutor wired to terminal capabilities."""
    skill = TerminalSkill(
        tool=tool,
        root_dir=root_dir,
        default_timeout=default_timeout,
        executor=executor,
    )
    return TaskExecutor(handler=TerminalTaskHandler(terminal_skill=skill))

