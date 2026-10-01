"""Application adapter registry for Mamba's cross-application capability.

An *adapter* is a small declarative record that tells the generic cross-app
driver how to recognise, launch, bind, and observe **one** application. It is
data, not a brain: no adapter plans, routes models, evaluates permissions, or
holds state. Adding support for another application means adding a record here —
Core, the permission system, and the verification framework are not modified.

There are deliberately no per-application agents (no ``NotepadBrain``,
``VSCodeBrain``, …). The execution path is always the same:

    adapter (identity + launch + observation probe)
      → generic cross-app driver (bind / focus / type)
        → existing Task → Skill → Tool pipeline
          → existing PermissionPolicy → existing Verifier

Identity rule for a *supported* application target — all three must hold:

1. the window's owning process name matches the adapter's ``process_names``,
2. the window class matches ``window_classes`` (when declared), and
3. the window title matches ``title_patterns`` (when declared).

Applications whose windows cannot be positively identified this way are not
targetable. An unknown or ambiguous window is never acted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import observation as obs
from ._win32 import WindowBinding, resolve_executable, title_matches

# ── supported application identifiers ──────────────────────────────────────
APP_NOTEPAD = "notepad"
APP_CALCULATOR = "calculator"
APP_FILE_EXPLORER = "file_explorer"
APP_VSCODE = "vscode"


@dataclass(frozen=True, slots=True)
class ApplicationAdapter:
    """Declarative description of one supported application."""

    app_id: str
    display_name: str
    description: str

    # ── identity ──
    process_names: tuple[str, ...]
    window_classes: tuple[str, ...] = ()
    title_patterns: tuple[str, ...] = ()

    # ── launch ──
    executable_names: tuple[str, ...] = ()
    executable_candidates: tuple[str, ...] = ()
    launch_args: tuple[str, ...] = ()
    launch_argument_builder: str = ""
    """Named generic builder for dynamic launch arguments (``"folder"``)."""

    # ── interaction ──
    supports_text_input: bool = False
    """Whether typing text into this application is a meaningful action."""

    # ── observation ──
    probes: tuple[obs.ObservationProbe, ...] = ()
    """Observation mechanisms, tried in order; the first available one wins."""

    supported_actions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def allows(self, window: WindowBinding) -> bool:
        """Whether a window positively identifies as this application."""
        process = (window.process_name or "").lower()
        if process not in {name.lower() for name in self.process_names}:
            return False
        if self.window_classes and window.class_name not in self.window_classes:
            return False
        if self.title_patterns and not title_matches(window.title, self.title_patterns):
            return False
        return True

    def primary_probe(self, window: WindowBinding) -> obs.ObservationProbe | None:
        """Return the first declared probe that can observe this window."""
        for probe in self.probes:
            try:
                available, _ = probe.available(window)
            except Exception:
                available = False
            if available:
                return probe
        return None

    def observe(
        self,
        window: WindowBinding,
        expected: str,
    ) -> obs.ObservationResult:
        """Observe an outcome for this application, honestly reporting limits."""
        probe = self.primary_probe(window)
        if probe is None:
            return obs.ObservationResult(
                status=obs.OBSERVATION_UNVERIFIED,
                detail=(
                    f"{self.display_name} exposes no reliable observation mechanism, so "
                    "the outcome could not be independently confirmed"
                ),
                source="none",
            )
        return probe.confirm(window, expected)

    def read_content(self, window: WindowBinding) -> tuple[str | None, str]:
        """Read this application's currently observable content."""
        probe = self.primary_probe(window)
        if probe is None:
            return None, (
                f"{self.display_name} exposes no reliable observation mechanism"
            )
        return probe.observe(window)

    def executable(self):
        """Resolve this application's executable, if it is installed."""
        return resolve_executable(self.executable_candidates, self.executable_names)

    def is_installed(self) -> bool:
        return self.executable() is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "name": self.display_name,
            "description": self.description,
            "installed": self.is_installed(),
            "supports_text_input": self.supports_text_input,
            "supported_actions": list(self.supported_actions),
            "observation": [probe.label for probe in self.probes],
            "notes": list(self.notes),
        }


def _notepad() -> ApplicationAdapter:
    return ApplicationAdapter(
        app_id=APP_NOTEPAD,
        display_name="Notepad",
        description="Windows text editor.",
        process_names=("Notepad.exe",),
        window_classes=("Notepad",),
        title_patterns=("notepad",),
        executable_names=("notepad.exe",),
        supports_text_input=True,
        probes=(
            obs.TextControlProbe(
                class_chain=("NotepadTextBox", "RichEditD2DPT"),
                label="Notepad editor text",
            ),
            obs.TextControlProbe(
                control_classes=("RichEditD2DPT", "RichEdit50W", "RichEdit20W", "Edit"),
                label="Notepad editor text",
            ),
        ),
        supported_actions=(
            "launch_application",
            "type_text",
            "read_application_text",
            "inspect_window",
        ),
        notes=(
            "Text typed into Notepad is read back from its editor control.",
        ),
    )


def _calculator() -> ApplicationAdapter:
    return ApplicationAdapter(
        app_id=APP_CALCULATOR,
        display_name="Calculator",
        description="Windows Calculator (display value observable via its copy command).",
        # UWP windows are hosted by ApplicationFrameHost; the title carries the app.
        process_names=("ApplicationFrameHost.exe",),
        window_classes=("ApplicationFrameWindow",),
        title_patterns=("calculator",),
        executable_names=("calc.exe",),
        supports_text_input=False,
        probes=(
            obs.ClipboardCopyProbe(
                label="Calculator display (Copied via Ctrl+C)",
            ),
        ),
        supported_actions=("launch_application", "inspect_window"),
        notes=(
            "Calculator has no Win32 text control; its display value is observed by "
            "invoking its native Ctrl+C copy command and reading the clipboard.",
            "Because it exposes no text field, typing arbitrary text is not a supported "
            "action — only its own keyboard input works.",
        ),
    )


def _file_explorer() -> ApplicationAdapter:
    return ApplicationAdapter(
        app_id=APP_FILE_EXPLORER,
        display_name="File Explorer",
        description="Windows File Explorer folder windows.",
        process_names=("explorer.exe",),
        window_classes=("CabinetWClass", "ExploreWClass"),
        executable_names=("explorer.exe",),
        launch_argument_builder="folder",
        probes=(
            obs.TextControlProbe(
                control_classes=("Edit", "ToolbarWindow32"),
                label="File Explorer address bar",
            ),
            obs.WindowStateProbe(label="File Explorer window state"),
        ),
        supported_actions=("launch_application", "inspect_window"),
        notes=(
            "Folder navigation is verified by the folder window that opens "
            "(window title = current folder).",
        ),
    )


def _vscode() -> ApplicationAdapter:
    return ApplicationAdapter(
        app_id=APP_VSCODE,
        display_name="Visual Studio Code",
        description="VS Code editor windows.",
        process_names=("Code.exe",),
        window_classes=("Chrome_WidgetWin_1",),
        title_patterns=("visual studio code",),
        executable_candidates=(
            "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
            "%PROGRAMFILES%\\Microsoft VS Code\\Code.exe",
        ),
        executable_names=("code.cmd", "code.exe"),
        supports_text_input=False,
        probes=(
            # VS Code renders with Electron; there is no Win32 text control to read,
            # so only the window itself is observable.
            obs.WindowStateProbe(label="VS Code window state"),
        ),
        supported_actions=("launch_application", "inspect_window"),
        notes=(
            "VS Code is an Electron application: its editor text is not exposed through "
            "a Win32 control, so interactions that require observing content are "
            "reported as executed-but-unverified rather than assumed successful.",
        ),
    )


_ADAPTERS: tuple[ApplicationAdapter, ...] = (
    _notepad(),
    _calculator(),
    _file_explorer(),
    _vscode(),
)


class ApplicationRegistry:
    """Registry of supported applications (adapters) with alias resolution."""

    def __init__(self, adapters: tuple[ApplicationAdapter, ...] | None = None) -> None:
        self._adapters: dict[str, ApplicationAdapter] = {}
        for adapter in adapters if adapters is not None else _ADAPTERS:
            self.register(adapter)

    def register(self, adapter: ApplicationAdapter, *, overwrite: bool = True) -> None:
        key = adapter.app_id.strip().lower()
        if not overwrite and key in self._adapters:
            raise ValueError(f"Application '{adapter.app_id}' is already registered")
        self._adapters[key] = adapter

    def get(self, app_id: str | None) -> ApplicationAdapter | None:
        if not app_id:
            return None
        return self._adapters.get(str(app_id).strip().lower())

    def all(self) -> tuple[ApplicationAdapter, ...]:
        return tuple(self._adapters.values())

    def resolve(self, name: str | None) -> ApplicationAdapter | None:
        """Resolve a user/planner-supplied application name to an adapter."""
        if not name:
            return None
        raw = str(name).strip().lower()
        if not raw:
            return None
        direct = self.get(raw)
        if direct is not None:
            return direct
        # Strip common decorations: "notepad.exe", "windows calculator", "the notepad app".
        clean = raw
        if clean.endswith(".exe"):
            clean = clean[:-4]
        for prefix in ("the ", "windows ", "microsoft "):
            while clean.startswith(prefix):
                clean = clean[len(prefix) :]
        for suffix in (" app", " application", " window"):
            while clean.endswith(suffix):
                clean = clean[: -len(suffix)]
        clean = clean.strip()
        if clean in _ALIASES:
            return self.get(_ALIASES[clean])
        for adapter in self._adapters.values():
            candidates = {adapter.app_id, adapter.display_name.lower()}
            candidates.update(alias for alias, target in _ALIASES.items() if target == adapter.app_id)
            if clean in candidates:
                return adapter
        return None

    def identify(self, window: WindowBinding) -> ApplicationAdapter | None:
        """Identify which supported application owns a window, if any."""
        for adapter in self._adapters.values():
            try:
                if adapter.allows(window):
                    return adapter
            except Exception:
                continue
        return None

    def describe(self) -> list[dict[str, Any]]:
        return [adapter.to_dict() for adapter in self._adapters.values()]

    def summary_for_planner(self) -> str:
        lines = ["SUPPORTED APPLICATIONS (cross-application interaction):"]
        for adapter in self._adapters.values():
            state = "installed" if adapter.is_installed() else "not installed"
            actions = ", ".join(adapter.supported_actions) or "none"
            lines.append(
                f"- {adapter.app_id} ({adapter.display_name}, {state}): {actions}"
            )
        return "\n".join(lines)


_ALIASES: dict[str, str] = {
    "notepad": APP_NOTEPAD,
    "text editor": APP_NOTEPAD,
    "calc": APP_CALCULATOR,
    "calculator": APP_CALCULATOR,
    "explorer": APP_FILE_EXPLORER,
    "file explorer": APP_FILE_EXPLORER,
    "files": APP_FILE_EXPLORER,
    "folder": APP_FILE_EXPLORER,
    "vscode": APP_VSCODE,
    "vs code": APP_VSCODE,
    "code": APP_VSCODE,
    "visual studio code": APP_VSCODE,
}


def default_application_registry() -> ApplicationRegistry:
    """Create the standard registry of supported applications."""
    return ApplicationRegistry()


__all__ = [
    "APP_CALCULATOR",
    "APP_FILE_EXPLORER",
    "APP_NOTEPAD",
    "APP_VSCODE",
    "ApplicationAdapter",
    "ApplicationRegistry",
    "default_application_registry",
]
