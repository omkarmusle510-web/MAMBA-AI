"""OCR tools for Mamba — extract text from screen captures."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import OCRError, RegionValidationError
from .screenshot import (
    _is_windows,
    _prepare_desktop,
    _validate_region,
    clear_latest_capture,
    get_latest_capture,
)
from .types import SCREEN_OPERATIONS, ScreenAction

_MAX_OCR_CHARS = 3000


def _find_tesseract_exe() -> str | None:
    """Probe standard locations for the Tesseract binary."""
    if t := shutil.which("tesseract"):
        return t
    if platform.system() == "Windows":
        prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidates = [
            os.path.join(prog_files, "Tesseract-OCR", "tesseract.exe"),
            os.path.join(prog_files_x86, "Tesseract-OCR", "tesseract.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    if platform.system() == "Linux" and os.path.exists("/usr/bin/tesseract"):
        return "/usr/bin/tesseract"
    return None


def _check_tesseract() -> tuple[bool, bool, str | None]:
    """Check pytesseract package and Tesseract engine availability.

    Returns (package_available, engine_available, exe_path).
    """
    try:
        import pytesseract as _pt  # noqa: F401
    except ImportError:
        return False, False, None

    exe = os.environ.get("TESSERACT_PATH") or _find_tesseract_exe()
    if exe:
        return True, True, exe

    # Package present but engine binary not found
    return True, False, None


def _trim_ocr_text(text: str, max_chars: int = _MAX_OCR_CHARS) -> str:
    """Strip empty lines and truncate OCR output."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "…"
    return out


def _run_ocr_on_image(image: Any) -> ToolOutput:
    """Perform OCR on a PIL Image, returning a ToolOutput."""
    package_available, engine_available, exe_path = _check_tesseract()

    if not package_available:
        return ToolOutput(
            success=True,
            result=(
                "OCR unavailable: the pytesseract Python package is not installed. "
                "Install it with: pip install pytesseract"
            ),
            metadata={
                "ocr_available": False,
                "package_available": False,
                "engine_available": False,
            },
        )

    if not engine_available:
        return ToolOutput(
            success=True,
            result=(
                "OCR engine (Tesseract) is not installed. "
                "The pytesseract Python package is available, but the actual "
                "Tesseract binary was not found on this system. "
                "On Windows, install from: "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            ),
            metadata={
                "ocr_available": False,
                "package_available": True,
                "engine_available": False,
            },
        )

    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = exe_path

    try:
        raw_text = pytesseract.image_to_string(image)
    except Exception as exc:
        return ToolOutput(
            success=False,
            error=f"Tesseract OCR failed: {exc}",
            metadata={"ocr_available": True, "error": str(exc)},
        )

    trimmed = _trim_ocr_text(raw_text)
    width, height = image.size

    return ToolOutput(
        success=True,
        result=trimmed if trimmed else "(no readable text detected)",
        metadata={
            "ocr_available": True,
            "package_available": True,
            "engine_available": True,
            "text": trimmed,
            "text_length": len(trimmed),
            "source_width": width,
            "source_height": height,
        },
    )


class OCRHandler:
    """Handler for full-screen OCR: captures screen then extracts text."""

    def run(self, input: ToolInput) -> ToolOutput:
        supplied_image = (
            input.arguments.get("_image")
            or input.arguments.get("image")
            or input.metadata.get("_image")
            or get_latest_capture()
        )
        owns_image = False
        image: Any = None

        if supplied_image is not None:
            image = supplied_image
        else:
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
                owns_image = True
            except Exception as exc:
                return ToolOutput(
                    success=False,
                    error=f"Screen capture failed: {exc}",
                    metadata={"error": str(exc)},
                )

        try:
            result = _run_ocr_on_image(image)
            result.metadata.setdefault("source_width", image.size[0])
            result.metadata.setdefault("source_height", image.size[1])
            return result
        finally:
            clear_latest_capture()
            if owns_image and image is not None:
                try:
                    image.close()
                except Exception:
                    pass


class RegionOCRHandler:
    """Handler for region OCR: captures a screen region then extracts text."""

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
        image: Any = None
        try:
            _prepare_desktop()
            image = ImageGrab.grab(bbox=bbox, all_screens=False)
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=f"Region capture failed: {exc}",
                metadata={"error": str(exc), "bbox": bbox},
            )

        try:
            result = _run_ocr_on_image(image)
            result.metadata.setdefault("x", x)
            result.metadata.setdefault("y", y)
            return result
        finally:
            if image is not None:
                try:
                    image.close()
                except Exception:
                    pass


class OCRTool(BaseTool):
    def __init__(self) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.OCR]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=OCRHandler(),
        )


class RegionOCRTool(BaseTool):
    def __init__(self) -> None:
        defn = SCREEN_OPERATIONS[ScreenAction.REGION_OCR]
        super().__init__(
            tool=Tool(
                name=defn.name,
                description=defn.description,
                metadata=defn.to_metadata(),
            ),
            handler=RegionOCRHandler(),
        )

