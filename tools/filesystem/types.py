"""Types and definitions for filesystem tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class FilesystemAction(StrEnum):
    """Supported filesystem operations."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIRECTORY = "list_directory"
    CREATE_DIRECTORY = "create_directory"
    DELETE = "delete"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True)
class FilesystemOperationDefinition:
    """Metadata definition for a filesystem operation."""

    name: str
    description: str
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Convert definition to metadata dictionary."""
        return {
            "action": self.name,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
        }


FILESYSTEM_OPERATIONS: dict[FilesystemAction, FilesystemOperationDefinition] = {
    FilesystemAction.READ_FILE: FilesystemOperationDefinition(
        name=FilesystemAction.READ_FILE.value,
        description="Read the text content of a regular file.",
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
    FilesystemAction.WRITE_FILE: FilesystemOperationDefinition(
        name=FilesystemAction.WRITE_FILE.value,
        description="Write text content to a file.",
        destructive=True,
        user_sensitive=True,
        irreversible=False,
    ),
    FilesystemAction.LIST_DIRECTORY: FilesystemOperationDefinition(
        name=FilesystemAction.LIST_DIRECTORY.value,
        description="List entries in a directory in deterministic order.",
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
    FilesystemAction.CREATE_DIRECTORY: FilesystemOperationDefinition(
        name=FilesystemAction.CREATE_DIRECTORY.value,
        description="Create a directory and any necessary parent directories.",
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
    FilesystemAction.DELETE: FilesystemOperationDefinition(
        name=FilesystemAction.DELETE.value,
        description="Delete a file or an empty directory.",
        destructive=True,
        user_sensitive=True,
        irreversible=True,
    ),
    FilesystemAction.EXISTS: FilesystemOperationDefinition(
        name=FilesystemAction.EXISTS.value,
        description="Check if a path exists on the filesystem.",
        destructive=False,
        user_sensitive=False,
        irreversible=False,
    ),
}


@dataclass(frozen=True, slots=True)
class FilesystemConfig:
    """Configuration for filesystem capability."""

    root_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.root_dir is not None:
            if not isinstance(self.root_dir, (str, Path)):
                raise ValueError(
                    f"root_dir must be a str or Path, got {type(self.root_dir).__name__}"
                )
            resolved = Path(self.root_dir).resolve()
            object.__setattr__(self, "root_dir", resolved)

