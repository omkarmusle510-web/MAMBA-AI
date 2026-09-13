"""Types and definitions for terminal tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import TerminalValidationError

TERMINAL_TOOL_METADATA: dict[str, Any] = {
    "action": "execute_command",
    "destructive": False,
    "user_sensitive": False,
    "irreversible": False,
    "risk_level": "medium",
}

READ_ONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "branch",
        "show",
        "version",
        "rev-parse",
        "describe",
        "ls-files",
        "help",
    }
)

SYSTEM_READ_COMMANDS: frozenset[str] = frozenset(
    {
        "echo",
        "dir",
        "type",
        "cat",
        "ls",
        "pwd",
        "whoami",
        "hostname",
        "uname",
        "where",
        "which",
        "ipconfig",
        "ifconfig",
        "uptime",
        "date",
        "time",
        "id",
    }
)

DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset(
    {
        "del",
        "rm",
        "rmdir",
        "erase",
        "format",
        "shred",
    }
)


def classify_terminal_command(
    executable: str,
    args: Sequence[str] = (),
) -> dict[str, Any]:
    """Classify risk and safety metadata dynamically for a terminal command."""
    exe_name = Path(executable).name.lower()
    exe_stem = Path(executable).stem.lower()

    clean_args = [str(a).strip() for a in args]

    # Git command inspection
    if exe_stem == "git" or exe_name.startswith("git"):
        subcommand = ""
        sub_idx = 0
        i = 0
        while i < len(clean_args):
            arg = clean_args[i]
            if arg in ("-C", "-c", "--git-dir", "--work-tree"):
                i += 2
                continue
            if arg.startswith("-"):
                i += 1
                continue
            subcommand = arg.lower()
            sub_idx = i
            break

        remaining_args = [a.lower() for a in clean_args[sub_idx + 1:]] if subcommand else []

        # 1. git reset --hard
        if subcommand == "reset" and "--hard" in remaining_args:
            return {
                "action": "git_reset_hard",
                "destructive": True,
                "user_sensitive": True,
                "irreversible": True,
                "risk_level": "high",
            }

        # 2. git clean -f, -fd, -fx, etc.
        if subcommand == "clean" and any(
            "-f" in a or a.startswith("-f") or "--force" in a for a in remaining_args
        ):
            return {
                "action": "git_clean_force",
                "destructive": True,
                "user_sensitive": True,
                "irreversible": True,
                "risk_level": "high",
            }

        # 3. branch deletion: git branch -d / -D / --delete
        if subcommand == "branch" and any(
            a in ("-d", "-D", "--delete") or a.startswith("-d") or a.startswith("-D")
            for a in remaining_args
        ):
            return {
                "action": "git_branch_delete",
                "destructive": True,
                "user_sensitive": False,
                "irreversible": True,
                "risk_level": "high",
            }

        # 4. force push: git push --force / -f
        if subcommand == "push" and any(
            a in ("--force", "-f", "--force-with-lease") or a.startswith("-f")
            for a in remaining_args
        ):
            return {
                "action": "git_push_force",
                "destructive": True,
                "user_sensitive": True,
                "irreversible": True,
                "risk_level": "high",
            }

        # 5. git config: distinguish read-only inspection from mutating configuration
        if subcommand == "config":
            read_flags = {"--get", "--get-all", "--get-regexp", "-l", "--list", "--get-color", "--get-urlmatch"}
            if any(flag in remaining_args for flag in read_flags) or not remaining_args:
                return {
                    "action": "git_config_read",
                    "destructive": False,
                    "user_sensitive": False,
                    "irreversible": False,
                    "risk_level": "low",
                }
            return {
                "action": "git_config_write",
                "destructive": False,
                "user_sensitive": False,
                "irreversible": False,
                "risk_level": "medium",
            }

        # 6. git tag: distinguish tag deletion (high), listing (low), and tag creation (medium)
        if subcommand == "tag":
            if any(a in ("-d", "-D", "--delete") or a.startswith("-d") or a.startswith("-D") for a in remaining_args):
                return {
                    "action": "git_tag_delete",
                    "destructive": True,
                    "user_sensitive": False,
                    "irreversible": True,
                    "risk_level": "high",
                }
            if not remaining_args or any(a in ("-l", "--list", "-n") for a in remaining_args):
                return {
                    "action": "git_tag_list",
                    "destructive": False,
                    "user_sensitive": False,
                    "irreversible": False,
                    "risk_level": "low",
                }
            return {
                "action": "git_tag_create",
                "destructive": False,
                "user_sensitive": False,
                "irreversible": False,
                "risk_level": "medium",
            }

        # 7. git remote: distinguish mutating remote actions from read-only inspections
        if subcommand == "remote":
            mutating_remote_actions = {"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune"}
            if any(a in mutating_remote_actions for a in remaining_args):
                return {
                    "action": "git_remote_modify",
                    "destructive": False,
                    "user_sensitive": False,
                    "irreversible": False,
                    "risk_level": "medium",
                }
            return {
                "action": "git_remote_view",
                "destructive": False,
                "user_sensitive": False,
                "irreversible": False,
                "risk_level": "low",
            }

        # Read-only git operations
        if subcommand in READ_ONLY_GIT_SUBCOMMANDS or not subcommand:
            return {
                "action": f"git_{subcommand}" if subcommand else "git",
                "destructive": False,
                "user_sensitive": False,
                "irreversible": False,
                "risk_level": "low",
            }

        # Non-destructive normal git operations (add, commit, checkout, switch, pull, fetch, clone, etc.)
        return {
            "action": f"git_{subcommand}",
            "destructive": False,
            "user_sensitive": False,
            "irreversible": False,
            "risk_level": "medium",
        }

    # Explicit destructive system commands
    if exe_stem in DESTRUCTIVE_COMMANDS or exe_name in DESTRUCTIVE_COMMANDS:
        return {
            "action": "execute_command",
            "destructive": True,
            "user_sensitive": True,
            "irreversible": True,
            "risk_level": "high",
        }

    # System read-only information commands
    if exe_stem in SYSTEM_READ_COMMANDS or exe_name in SYSTEM_READ_COMMANDS:
        return {
            "action": "execute_command",
            "destructive": False,
            "user_sensitive": False,
            "irreversible": False,
            "risk_level": "low",
        }

    # Default for normal development commands
    return {
        "action": "execute_command",
        "destructive": False,
        "user_sensitive": False,
        "irreversible": False,
        "risk_level": "medium",
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
