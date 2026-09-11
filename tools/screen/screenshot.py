"""Screenshot capture tools for Mamba."""

from __future__ import annotations

import sys
from typing import Any

from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import RegionValidationError, ScreenCaptureError
from .types import SCREEN_OPERATIONS, ScreenAction


def _is_windows() -> bool:
    return sys.platform == "win32"


def _prepare_desktop() -> None:
    """Ensure current thread is attached to the active input desktop on Windows."""
    if _is_windows():
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass


def _validate_region(arguments: dict[str, Any]) -> tuple[int, int, int, int]:
    """Validate and extract region coordinates from arguments.

    Expects x, y, width, height — all non-negative integers with width/height > 0.
    Returns (x, y, width, height).
    """
    missing = [k for k in ("x", "y", "width", "height") if k not in arguments]
    if missing:
        raise RegionValidationError(
            f"Missing required region argument(s): {', '.join(missing)}. "
            "Expected: x, y, width, height (all integers)."
        )

    try:
        x = int(arguments["x"])
        y = int(arguments["y"])
        width = int(arguments["width"])
        height = int(arguments["height"])
    except (TypeError, ValueError) as exc:
        raise RegionValidationError(
            f"Region coordinates must be integers: {exc}"
        ) from exc

    if x < 0 or y < 0:
        raise RegionValidationError(
            f"Region origin must be non-negative: x={x}, y={y}"
        )
    if width <= 0 or height <= 0:
        raise RegionValidationError(
            f"Region dimensions must be positive: width={width}, height={height}"
        )

    return x, y, width, height


_latest_capture: Any = None


def get_latest_capture() -> Any:
    """Return the most recently captured in-memory screenshot, if any."""
    return _latest_capture


def _set_latest_capture(image: Any) -> None:
    """Store the latest capture and safely close any previous unconsumed capture."""
    global _latest_capture
    if _latest_capture is not None and _latest_capture is not image:
        try:
            _latest_capture.close()
        except Exception:
            pass
    _latest_capture = image


def clear_latest_capture() -> None:
    """Safely close and clear the latest captured screenshot from memory."""
    global _latest_capture
    if _latest_capture is not None:
        try:
            _latest_capture.close()
        except Exception:
            pass
        _latest_capture = None


class ScreenshotHandler:
    """Handler for full-screen screenshot capture."""

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Screen capture is only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            from PIL import ImageGrab
        except ImportError:
            return ToolOutput(
                success=False,
                error="Pillow (PIL) is not installed. Screen capture requires Pillow.",
                metadata={"available": False},
            )

        try:
            _prepare_desktop()
            image = ImageGrab.grab(all_screens=False)
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Screen capture failed: {exc}",
                metadata={"error": str(exc)},
            )

        _set_latest_capture(image)
        width, height = image.size
        return ToolOutput(
            success=True,
            result=f"Screenshot captured: {width}x{height} pixels, PNG format.",
            metadata={
                "width": width,
                "height": height,
                "format": "PNG",
            },
        )


class RegionScreenshotHandler:
    """Handler for region screenshot capture."""

    def run(self, input: ToolInput) -> ToolOutput:
        if not _is_windows():
            return ToolOutput(
                success=False,
                error="Screen capture is only supported on Windows.",
                metadata={"available": False, "platform": sys.platform},
            )

        try:
            from PIL import ImageGrab
        except ImportError:
            return ToolOutput(
                success=False,
                error="Pillow (PIL) is not installed. Screen capture requires Pillow.",
                metadata={"available": False},
            )

        try:
            x, y, width, height = _validate_region(input.arguments)
        except RegionValidationError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                metadata={"validation_error": True},
            )

        bbox = (x, y, x + width, y + height)
        try:
            _prepare_desktop()
            image = ImageGrab.grab(bbox=bbox, all_screens=False)
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Region capture failed: {exc}",
                metadata={"error": str(exc), "bbox": bbox},
            )

        _set_latest_capture(image)
        actual_w, actual_h = image.size
        return ToolOutput(
            success=True,
            result=(
                f"Region screenshot captured: {actual_w}x{actual_h} pixels "
                f"at ({x}, {y}), PNG format."
            ),
            metadata={
                "x": x,
                "y": y,
                "width": actual_w,
                "height": actual_h,
                "format": "PNG",
            },
        )


class ScreenshotTool(BaseTool):
    def __init__(self) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.SCREENSHOT]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=ScreenshotHandler(),
        )


class RegionScreenshotTool(BaseTool):
    def __init__(self) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.REGION_SCREENSHOT]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=RegionScreenshotHandler(),
        )

