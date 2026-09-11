"""Clipboard tools for Mamba."""

from __future__ import annotations

from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import ClipboardError
from .types import DESKTOP_OPERATIONS, DesktopAction


class ReadClipboardHandler:
    """Handler for reading system clipboard content."""

    def run(self, input: ToolInput) -> ToolOutput:
        try:
            import pyperclip
        except ImportError:
            return ToolOutput(
                success=False,
                error="pyperclip package is not installed.",
                metadata={"available": False},
            )

        try:
            text = pyperclip.paste()
            if text is None:
                text = ""
            return ToolOutput(
                success=True,
                result=text,
                metadata={
                    "text": text,
                    "length": len(text),
                    "actual": text,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Clipboard read failed: {exc}",
                metadata={"error": str(exc)},
            )


class WriteClipboardHandler:
    """Handler for writing text to system clipboard."""

    def run(self, input: ToolInput) -> ToolOutput:
        try:
            import pyperclip
        except ImportError:
            return ToolOutput(
                success=False,
                error="pyperclip package is not installed.",
                metadata={"available": False},
            )

        text_arg = input.arguments.get("text")
        if text_arg is None:
            text = ""
        elif isinstance(text_arg, str):
            text = text_arg
        else:
            text = str(text_arg)

        try:
            pyperclip.copy(text)
            return ToolOutput(
                success=True,
                result=f"Wrote {len(text)} character(s) to clipboard.",
                metadata={
                    "text": text,
                    "length": len(text),
                    "actual": text,
                    "expected": text,
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Clipboard write failed: {exc}",
                metadata={"error": str(exc)},
            )


class ClearClipboardHandler:
    """Handler for clearing system clipboard content."""

    def run(self, input: ToolInput) -> ToolOutput:
        try:
            import pyperclip
        except ImportError:
            return ToolOutput(
                success=False,
                error="pyperclip package is not installed.",
                metadata={"available": False},
            )

        try:
            pyperclip.copy("")
            return ToolOutput(
                success=True,
                result="Clipboard cleared.",
                metadata={
                    "cleared": True,
                    "text": "",
                    "length": 0,
                    "actual": "",
                    "expected": "",
                },
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Clipboard clear failed: {exc}",
                metadata={"error": str(exc)},
            )


class ReadClipboardTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.READ_CLIPBOARD]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=ReadClipboardHandler(),
        )


class WriteClipboardTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.WRITE_CLIPBOARD]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=WriteClipboardHandler(),
        )


class ClearClipboardTool(BaseTool):
    def __init__(self) -> None:
        defn = DESKTOP_OPERATIONS[DesktopAction.CLEAR_CLIPBOARD]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=ClearClipboardHandler(),
        )

