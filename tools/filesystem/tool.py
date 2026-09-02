"""Filesystem tools implementation for Mamba."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import FilesystemError, FilesystemOperationError, FilesystemPathError
from .types import FILESYSTEM_OPERATIONS, FilesystemAction


def resolve_secure_path(path: Any, root_dir: Path | str | None = None) -> Path:
    """Validate and resolve a path against an optional root directory.

    When root_dir is configured:
    1. Resolve the root.
    2. Resolve the requested path.
    3. Verify the requested path remains inside the configured root.
    4. Reject path traversal.
    5. Reject paths escaping the configured root.

    When no root_dir is configured:
    - operate only on the explicitly supplied path
    - do not silently expand or reinterpret paths
    """
    if path is None:
        raise FilesystemPathError("Path argument is required")
    if not isinstance(path, (str, Path)):
        raise FilesystemPathError(
            f"Path must be a str or Path, got {type(path).__name__}"
        )

    if isinstance(path, str) and not path.strip():
        raise FilesystemPathError("Path must not be empty")

    try:
        raw_path = Path(path)
    except Exception as exc:
        raise FilesystemPathError(f"Invalid path '{path}': {exc}") from exc

    if root_dir is not None:
        resolved_root = Path(root_dir).resolve()
        if raw_path.is_absolute():
            resolved_path = raw_path.resolve()
        else:
            resolved_path = (resolved_root / raw_path).resolve()

        if not resolved_path.is_relative_to(resolved_root):
            raise FilesystemPathError(
                f"Path '{path}' escapes configured root '{resolved_root}'"
            )
        return resolved_path

    return raw_path


class BaseFilesystemHandler:
    """Base handler providing path resolution and input extraction."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root_dir = Path(root_dir).resolve() if root_dir is not None else None

    @property
    def root_dir(self) -> Path | None:
        return self._root_dir

    def _resolve_path(self, path: Any) -> Path:
        return resolve_secure_path(path, self._root_dir)

    def _get_path_arg(self, input: ToolInput) -> Path:
        if not isinstance(input.arguments, dict):
            raise FilesystemPathError("Tool arguments must be a dictionary")
        if "path" not in input.arguments:
            raise FilesystemPathError("Missing required argument: 'path'")
        return self._resolve_path(input.arguments["path"])


class ReadFileHandler(BaseFilesystemHandler):
    """Handler for read_file operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        target_path = self._get_path_arg(input)
        encoding = input.arguments.get("encoding", "utf-8")
        if encoding is None:
            encoding = "utf-8"
        if not isinstance(encoding, str):
            raise FilesystemOperationError(
                f"encoding must be a string, got {type(encoding).__name__}"
            )

        if not target_path.exists():
            raise FilesystemOperationError(f"File not found: '{target_path}'")

        if not target_path.is_file():
            raise FilesystemOperationError(
                f"Path is not a regular file: '{target_path}'"
            )

        try:
            content = target_path.read_text(encoding=encoding)
        except Exception as exc:
            raise FilesystemOperationError(
                f"Failed to read file '{target_path}': {exc}"
            ) from exc

        return ToolOutput(
            success=True,
            result={
                "path": str(target_path),
                "content": content,
            },
            metadata=FILESYSTEM_OPERATIONS[FilesystemAction.READ_FILE].to_metadata(),
        )


class WriteFileHandler(BaseFilesystemHandler):
    """Handler for write_file operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        target_path = self._get_path_arg(input)
        if "content" not in input.arguments:
            raise FilesystemOperationError("Missing required argument: 'content'")

        content = input.arguments["content"]
        if not isinstance(content, str):
            raise FilesystemOperationError(
                f"content must be a string, got {type(content).__name__}"
            )

        encoding = input.arguments.get("encoding", "utf-8")
        if encoding is None:
            encoding = "utf-8"
        if not isinstance(encoding, str):
            raise FilesystemOperationError(
                f"encoding must be a string, got {type(encoding).__name__}"
            )

        parent = target_path.parent
        if not parent.exists():
            raise FilesystemOperationError(
                f"Parent directory does not exist: '{parent}'"
            )
        if not parent.is_dir():
            raise FilesystemOperationError(
                f"Parent path is not a directory: '{parent}'"
            )

        if target_path.exists() and not target_path.is_file():
            raise FilesystemOperationError(
                f"Cannot write file, path is an existing directory: '{target_path}'"
            )

        try:
            encoded_bytes = content.encode(encoding)
        except Exception as exc:
            raise FilesystemOperationError(f"Encoding failed: {exc}") from exc

        try:
            target_path.write_bytes(encoded_bytes)
        except Exception as exc:
            raise FilesystemOperationError(
                f"Failed to write file '{target_path}': {exc}"
            ) from exc

        return ToolOutput(
            success=True,
            result={
                "path": str(target_path),
                "bytes_written": len(encoded_bytes),
            },
            metadata=FILESYSTEM_OPERATIONS[FilesystemAction.WRITE_FILE].to_metadata(),
        )


class ListDirectoryHandler(BaseFilesystemHandler):
    """Handler for list_directory operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        target_path = self._get_path_arg(input)

        if not target_path.exists():
            raise FilesystemOperationError(f"Directory not found: '{target_path}'")

        if not target_path.is_dir():
            raise FilesystemOperationError(
                f"Path is not a directory: '{target_path}'"
            )

        try:
            entries = sorted(entry.name for entry in target_path.iterdir())
        except Exception as exc:
            raise FilesystemOperationError(
                f"Failed to list directory '{target_path}': {exc}"
            ) from exc

        return ToolOutput(
            success=True,
            result={
                "path": str(target_path),
                "entries": entries,
            },
            metadata=FILESYSTEM_OPERATIONS[
                FilesystemAction.LIST_DIRECTORY
            ].to_metadata(),
        )


class CreateDirectoryHandler(BaseFilesystemHandler):
    """Handler for create_directory operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        target_path = self._get_path_arg(input)

        if target_path.exists():
            if not target_path.is_dir():
                raise FilesystemOperationError(
                    f"Cannot create directory, path is an existing file: '{target_path}'"
                )
            return ToolOutput(
                success=True,
                result={
                    "path": str(target_path),
                    "created": True,
                },
                metadata=FILESYSTEM_OPERATIONS[
                    FilesystemAction.CREATE_DIRECTORY
                ].to_metadata(),
            )

        for parent in target_path.parents:
            if parent.exists() and not parent.is_dir():
                raise FilesystemOperationError(
                    f"Cannot create directory, parent '{parent}' is an existing file"
                )

        try:
            target_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise FilesystemOperationError(
                f"Failed to create directory '{target_path}': {exc}"
            ) from exc

        return ToolOutput(
            success=True,
            result={
                "path": str(target_path),
                "created": True,
            },
            metadata=FILESYSTEM_OPERATIONS[
                FilesystemAction.CREATE_DIRECTORY
            ].to_metadata(),
        )


class DeleteHandler(BaseFilesystemHandler):
    """Handler for delete operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        target_path = self._get_path_arg(input)

        if not target_path.exists():
            raise FilesystemOperationError(f"Path not found: '{target_path}'")

        try:
            if target_path.is_dir():
                if any(target_path.iterdir()):
                    raise FilesystemOperationError(
                        f"Cannot delete non-empty directory: '{target_path}'"
                    )
                target_path.rmdir()
            else:
                target_path.unlink()
        except FilesystemOperationError:
            raise
        except Exception as exc:
            raise FilesystemOperationError(
                f"Failed to delete '{target_path}': {exc}"
            ) from exc

        return ToolOutput(
            success=True,
            result={
                "path": str(target_path),
                "deleted": True,
            },
            metadata=FILESYSTEM_OPERATIONS[FilesystemAction.DELETE].to_metadata(),
        )


class ExistsHandler(BaseFilesystemHandler):
    """Handler for exists operation."""

    def run(self, input: ToolInput) -> ToolOutput:
        target_path = self._get_path_arg(input)
        path_exists = target_path.exists()

        return ToolOutput(
            success=True,
            result={
                "path": str(target_path),
                "exists": path_exists,
            },
            metadata=FILESYSTEM_OPERATIONS[FilesystemAction.EXISTS].to_metadata(),
        )


class FilesystemHandler(BaseFilesystemHandler):
    """Unified handler dispatching to supported filesystem operations."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        super().__init__(root_dir=root_dir)
        self._handlers: dict[str, BaseFilesystemHandler] = {
            FilesystemAction.READ_FILE.value: ReadFileHandler(root_dir=root_dir),
            FilesystemAction.WRITE_FILE.value: WriteFileHandler(root_dir=root_dir),
            FilesystemAction.LIST_DIRECTORY.value: ListDirectoryHandler(
                root_dir=root_dir
            ),
            FilesystemAction.CREATE_DIRECTORY.value: CreateDirectoryHandler(
                root_dir=root_dir
            ),
            FilesystemAction.DELETE.value: DeleteHandler(root_dir=root_dir),
            FilesystemAction.EXISTS.value: ExistsHandler(root_dir=root_dir),
        }

    def run(self, input: ToolInput) -> ToolOutput:
        action = (
            input.arguments.get("action")
            or input.metadata.get("action")
            or input.arguments.get("operation")
        )
        if not action:
            raise FilesystemPathError(
                "Missing required action or operation in arguments or metadata"
            )
        if not isinstance(action, str):
            raise FilesystemOperationError(
                f"action must be a string, got {type(action).__name__}"
            )

        handler = self._handlers.get(action)
        if handler is None:
            raise FilesystemOperationError(
                f"Unsupported filesystem action: '{action}'"
            )

        return handler.run(input)


class ReadFileTool(BaseTool):
    """Tool for reading text files."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.READ_FILE]
        tool = Tool(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        handler = ReadFileHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


class WriteFileTool(BaseTool):
    """Tool for writing text files."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.WRITE_FILE]
        tool = Tool(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        handler = WriteFileHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


class ListDirectoryTool(BaseTool):
    """Tool for listing directory contents."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.LIST_DIRECTORY]
        tool = Tool(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        handler = ListDirectoryHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


class CreateDirectoryTool(BaseTool):
    """Tool for creating directories."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.CREATE_DIRECTORY]
        tool = Tool(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        handler = CreateDirectoryHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


class DeleteTool(BaseTool):
    """Tool for deleting files or empty directories."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.DELETE]
        tool = Tool(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        handler = DeleteHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


class ExistsTool(BaseTool):
    """Tool for checking path existence."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        defn = FILESYSTEM_OPERATIONS[FilesystemAction.EXISTS]
        tool = Tool(
            name=defn.name,
            description=defn.description,
            metadata=defn.to_metadata(),
        )
        handler = ExistsHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


class FilesystemTool(BaseTool):
    """Unified tool dispatching to supported filesystem operations."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        tool = Tool(
            name="filesystem",
            description="Perform filesystem operations (read_file, write_file, list_directory, create_directory, delete, exists).",
            metadata={"actions": [a.value for a in FilesystemAction]},
        )
        handler = FilesystemHandler(root_dir=root_dir)
        super().__init__(tool=tool, handler=handler)


def create_filesystem_tools(
    root_dir: str | Path | None = None,
) -> dict[str, BaseTool]:
    """Create all standard filesystem tools configured with an optional root directory."""
    return {
        FilesystemAction.READ_FILE.value: ReadFileTool(root_dir=root_dir),
        FilesystemAction.WRITE_FILE.value: WriteFileTool(root_dir=root_dir),
        FilesystemAction.LIST_DIRECTORY.value: ListDirectoryTool(root_dir=root_dir),
        FilesystemAction.CREATE_DIRECTORY.value: CreateDirectoryTool(root_dir=root_dir),
        FilesystemAction.DELETE.value: DeleteTool(root_dir=root_dir),
        FilesystemAction.EXISTS.value: ExistsTool(root_dir=root_dir),
    }

