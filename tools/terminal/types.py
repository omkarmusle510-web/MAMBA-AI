"""Types and definitions for terminal tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import TerminalValidationError

TERMINAL_TOOL_METADATA: dict[str, Any] = {
    "action": "execute_command",
    "destructive": True,
    "user_sensitive": True,
    "irreversible": False,
    "risk_level": "high",
}

DISALLOWED_SHELL_NAMES: frozenset[str] = frozenset(
    {
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "bash",
        "bash.exe",
        "sh",
        "sh.exe",
        "zsh",
        "zsh.exe",
    }
)

DISALLOWED_SHELL_STEMS: frozenset[str] = frozenset(
    {
        "cmd",
        "powershell",
        "pwsh",
        "bash",
        "sh",
        "zsh",
    }
)

DISALLOWED_SHELL_ARGS: frozenset[str] = frozenset(
    {
        "/c",
        "/k",
        "-command",
    }
)


def validate_non_shell_command(executable: str, args: Sequence[str]) -> None:
    """Validate that the executable and arguments do not invoke a shell interpreter or shell mode."""
    exe_path = Path(executable)
    exe_name = exe_path.name.lower()
    exe_stem = exe_path.stem.lower()

    if exe_name in DISALLOWED_SHELL_NAMES or exe_stem in DISALLOWED_SHELL_STEMS:
        raise TerminalValidationError(
            f"Shell interpreter '{executable}' is not permitted"
        )

    is_python = (
        exe_stem in {"python", "python3", "pythonw", "py"}
        or exe_name.startswith("python")
    )

    for arg in args:
        arg_lower = arg.lower()
        if arg_lower in DISALLOWED_SHELL_ARGS:
            raise TerminalValidationError(
                f"Shell-mode invocation argument '{arg}' is not permitted"
            )
        if arg == "-c" and not is_python:
            raise TerminalValidationError(
                f"Shell-mode invocation argument '{arg}' is not permitted"
            )

        arg_path = Path(arg)
        if (
            arg_path.name.lower() in DISALLOWED_SHELL_NAMES
            or arg_path.stem.lower() in DISALLOWED_SHELL_STEMS
        ):
            raise TerminalValidationError(
                f"Shell interpreter argument '{arg}' is not permitted"
            )


@dataclass(frozen=True, slots=True)
class TerminalCommand:
    """Specification of a command to be executed."""

    executable: str
    args: Sequence[str] = field(default_factory=tuple)
    cwd: str | Path | None = None
    timeout: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.executable, str):
            raise TerminalValidationError(
                f"executable must be a string, got {type(self.executable).__name__}"
            )
        if not self.executable.strip():
            raise TerminalValidationError("executable must not be empty")

        if not isinstance(self.args, (list, tuple)):
            raise TerminalValidationError(
                f"args must be a sequence of strings, got {type(self.args).__name__}"
            )
        for i, arg in enumerate(self.args):
            if not isinstance(arg, str):
                raise TerminalValidationError(
                    f"argument at index {i} must be a string, got {type(arg).__name__}"
                )
        if isinstance(self.args, list):
            object.__setattr__(self, "args", tuple(self.args))

        # Enforce non-shell contract
        validate_non_shell_command(self.executable, self.args)

        if self.timeout is not None:
            if isinstance(self.timeout, bool) or not isinstance(
                self.timeout, (int, float)
            ):
                raise TerminalValidationError(
                    f"timeout must be a positive number, got {type(self.timeout).__name__}"
                )
            if self.timeout <= 0:
                raise TerminalValidationError(
                    f"timeout must be a positive number, got {self.timeout}"
                )
            object.__setattr__(self, "timeout", float(self.timeout))

        if self.cwd is not None:
            if not isinstance(self.cwd, (str, Path)):
                raise TerminalValidationError(
                    f"cwd must be a string or Path, got {type(self.cwd).__name__}"
                )
            if isinstance(self.cwd, str) and not self.cwd.strip():
                raise TerminalValidationError("cwd must not be empty")

        if not isinstance(self.metadata, Mapping):
            raise TerminalValidationError(
                f"metadata must be a mapping, got {type(self.metadata).__name__}"
            )


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Structured outcome of a terminal command execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    command: TerminalCommand | None = None
    cwd: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary matching the standard output schema."""
        cmd_dict = {
            "executable": self.command.executable if self.command else "",
            "args": list(self.command.args) if self.command else [],
        }
        return {
            "command": cmd_dict,
            "cwd": self.cwd,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
        }


@dataclass(frozen=True, slots=True)
class TerminalConfig:
    """Configuration for terminal capability."""

    root_dir: Path | None = None
    default_timeout: float | None = None

    def __post_init__(self) -> None:
        if self.root_dir is not None:
            if not isinstance(self.root_dir, (str, Path)):
                raise ValueError(
                    f"root_dir must be a str or Path, got {type(self.root_dir).__name__}"
                )
            resolved = Path(self.root_dir).resolve()
            object.__setattr__(self, "root_dir", resolved)

        if self.default_timeout is not None:
            if isinstance(self.default_timeout, bool) or not isinstance(
                self.default_timeout, (int, float)
            ):
                raise ValueError(
                    f"default_timeout must be a positive number, got {type(self.default_timeout).__name__}"
                )
            if self.default_timeout <= 0:
                raise ValueError(
                    f"default_timeout must be a positive number, got {self.default_timeout}"
                )
            object.__setattr__(self, "default_timeout", float(self.default_timeout))
