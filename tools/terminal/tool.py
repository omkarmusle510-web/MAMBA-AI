"""Terminal tools implementation for Mamba."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import (
    TerminalError,
    TerminalExecutionError,
    TerminalTimeoutError,
    TerminalValidationError,
)
from .types import (
    TERMINAL_TOOL_METADATA,
    TerminalCommand,
    TerminalConfig,
    TerminalResult,
)


def _decode_stream(data: str | bytes | None) -> str:
    """Decode process output as UTF-8 with replacement for invalid bytes."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def resolve_working_directory(
    cwd: str | Path | None,
    root_dir: str | Path | None = None,
) -> Path | None:
    """Validate and resolve working directory against an optional root directory.

    When root_dir is configured:
    - Resolve the root.
    - If cwd is provided, resolve and verify cwd remains inside root.
    - Reject traversal outside root.
    - Reject non-existent cwd or cwd that is not a directory.
    - If cwd is not provided, default to root_dir.

    When no root_dir is configured:
    - If cwd is provided, verify it exists and is a directory.
    - Do not silently expand '~' or rewrite arbitrary paths.
    - If cwd is not provided, return None (subprocess defaults to current directory).
    """
    if cwd is not None:
        if not isinstance(cwd, (str, Path)):
            raise TerminalValidationError(
                f"cwd must be a string or Path, got {type(cwd).__name__}"
            )
        if isinstance(cwd, str) and not cwd.strip():
            raise TerminalValidationError("cwd must not be empty")

    if root_dir is not None:
        resolved_root = Path(root_dir).resolve()
        if not resolved_root.exists():
            raise TerminalValidationError(
                f"Configured root directory does not exist: '{resolved_root}'"
            )
        if not resolved_root.is_dir():
            raise TerminalValidationError(
                f"Configured root is not a directory: '{resolved_root}'"
            )

        if cwd is not None:
            raw_cwd = Path(cwd)
            if raw_cwd.is_absolute():
                resolved_cwd = raw_cwd.resolve()
            else:
                resolved_cwd = (resolved_root / raw_cwd).resolve()

            if not resolved_cwd.is_relative_to(resolved_root):
                raise TerminalValidationError(
                    f"Working directory '{cwd}' escapes configured root '{resolved_root}'"
                )
            if not resolved_cwd.exists():
                raise TerminalValidationError(
                    f"Working directory does not exist: '{resolved_cwd}'"
                )
            if not resolved_cwd.is_dir():
                raise TerminalValidationError(
                    f"Working directory is not a directory: '{resolved_cwd}'"
                )
            return resolved_cwd

        return resolved_root

    if cwd is not None:
        target_cwd = Path(cwd)
        if not target_cwd.exists():
            raise TerminalValidationError(
                f"Working directory does not exist: '{target_cwd}'"
            )
        if not target_cwd.is_dir():
            raise TerminalValidationError(
                f"Working directory is not a directory: '{target_cwd}'"
            )
        return target_cwd

    return None


def parse_terminal_command(input: ToolInput) -> TerminalCommand:
    """Extract and validate TerminalCommand from ToolInput arguments."""
    args_dict = input.arguments

    # Allow passing pre-constructed TerminalCommand
    cmd_obj = args_dict.get("command")
    if isinstance(cmd_obj, TerminalCommand):
        return cmd_obj

    executable: Any = None
    cmd_args: Any = None

    if "executable" in args_dict:
        executable = args_dict["executable"]
        cmd_args = args_dict.get("args", ())
    elif "command" in args_dict:
        val = args_dict["command"]
        if isinstance(val, (list, tuple)):
            if not val:
                raise TerminalValidationError("command list must not be empty")
            executable = val[0]
            extra_args = list(args_dict.get("args", ()))
            cmd_args = list(val[1:]) + extra_args
        else:
            executable = val
            cmd_args = args_dict.get("args", ())
    else:
        raise TerminalValidationError(
            "Missing required argument: 'executable' or 'command'"
        )

    cwd = args_dict.get("cwd")
    timeout = args_dict.get("timeout")
    metadata = args_dict.get("metadata", {})

    return TerminalCommand(
        executable=executable,
        args=cmd_args,
        cwd=cwd,
        timeout=timeout,
        metadata=metadata,
    )


class TerminalHandler:
    """Executes external commands via subprocess with shell=False."""

    def __init__(
        self,
        root_dir: str | Path | None = None,
        default_timeout: float | None = None,
    ) -> None:
        self._root_dir = Path(root_dir).resolve() if root_dir is not None else None
        self._default_timeout = (
            float(default_timeout) if default_timeout is not None else None
        )

    @property
    def root_dir(self) -> Path | None:
        return self._root_dir

    @property
    def default_timeout(self) -> float | None:
        return self._default_timeout

    def run(self, input: ToolInput) -> ToolOutput:
        """Run the requested command in a controlled subprocess."""
        if not isinstance(input.arguments, dict):
            raise TerminalValidationError("Tool arguments must be a dictionary")

        cmd = parse_terminal_command(input)
        effective_cwd = resolve_working_directory(cmd.cwd, self._root_dir)
        cwd_str = str(effective_cwd) if effective_cwd is not None else None

        timeout = cmd.timeout if cmd.timeout is not None else self._default_timeout
        raise_on_timeout = bool(input.arguments.get("raise_on_timeout", False))

        cmd_list = [cmd.executable, *cmd.args]

        try:
            proc = subprocess.run(
                cmd_list,
                cwd=cwd_str,
                timeout=timeout,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            exit_code: int | None = proc.returncode
            stdout = _decode_stream(proc.stdout)
            stderr = _decode_stream(proc.stderr)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = _decode_stream(exc.stdout)
            stderr = _decode_stream(exc.stderr)
            if raise_on_timeout:
                raise TerminalTimeoutError(
                    f"Command '{cmd.executable}' timed out after {timeout} seconds"
                ) from exc
        except FileNotFoundError as exc:
            raise TerminalExecutionError(
                f"Executable not found: '{cmd.executable}'"
            ) from exc
        except PermissionError as exc:
            raise TerminalExecutionError(
                f"Permission denied executing '{cmd.executable}': {exc}"
            ) from exc
        except OSError as exc:
            raise TerminalExecutionError(
                f"Failed to execute command '{cmd.executable}': {exc}"
            ) from exc

        success = (exit_code == 0) and (not timed_out)
        if timed_out:
            error_msg = f"Command '{cmd.executable}' timed out after {timeout} seconds"
        elif exit_code != 0:
            error_msg = (
                f"Command '{cmd.executable}' exited with non-zero exit code {exit_code}"
            )
        else:
            error_msg = None

        terminal_result = TerminalResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            command=cmd,
            cwd=cwd_str,
            metadata=dict(cmd.metadata),
        )

        output_metadata = {
            **TERMINAL_TOOL_METADATA,
            "executable": cmd.executable,
            "exit_code": exit_code,
            "timed_out": timed_out,
        }

        return ToolOutput(
            success=success,
            result=terminal_result.to_dict(),
            error=error_msg,
            metadata=output_metadata,
        )


class TerminalTool(BaseTool):
    """Tool for controlled execution of external terminal commands."""

    def __init__(
        self,
        root_dir: str | Path | None = None,
        default_timeout: float | None = None,
    ) -> None:
        tool = Tool(
            name="terminal",
            description="Execute a controlled command with explicit arguments without a shell.",
            metadata=dict(TERMINAL_TOOL_METADATA),
        )
        handler = TerminalHandler(
            root_dir=root_dir,
            default_timeout=default_timeout,
        )
        super().__init__(tool=tool, handler=handler)


def create_terminal_tool(
    root_dir: str | Path | None = None,
    default_timeout: float | None = None,
) -> TerminalTool:
    """Create a configured TerminalTool instance."""
    return TerminalTool(root_dir=root_dir, default_timeout=default_timeout)
