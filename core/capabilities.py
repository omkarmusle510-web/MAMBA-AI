"""Runtime Capability Registry and capability awareness for Mamba."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CapabilityStatus(str, Enum):
    """Runtime availability status of a Mamba capability."""

    AVAILABLE = "available"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """Describes one discoverable Mamba capability with its actions and limitations."""

    capability_id: str
    name: str
    description: str
    supported_actions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    unavailable_actions: tuple[str, ...] = ()
    status: CapabilityStatus = CapabilityStatus.AVAILABLE
    provider: str | None = None
    provider_configured: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be empty")
        if not self.name.strip():
            raise ValueError("name must not be empty")

    @property
    def is_available(self) -> bool:
        """Return True if capability is enabled, available, and its provider is configured."""
        return self.status == CapabilityStatus.AVAILABLE and self.provider_configured

    def to_dict(self) -> dict[str, Any]:
        """Serialize descriptor to dict."""
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "supported_actions": list(self.supported_actions),
            "limitations": list(self.limitations),
            "unavailable_actions": list(self.unavailable_actions),
            "status": self.status.value,
            "provider": self.provider,
            "provider_configured": self.provider_configured,
            "is_available": self.is_available,
            "metadata": dict(self.metadata),
        }

    def format_summary_line(self) -> str:
        """Format a concise one-line summary for model / planner context."""
        actions_str = ", ".join(self.supported_actions[:6])
        if len(self.supported_actions) > 6:
            actions_str += f", +{len(self.supported_actions) - 6} more"

        if self.status == CapabilityStatus.AVAILABLE and self.provider_configured:
            limit_str = f"; {self.limitations[0]}" if self.limitations else ""
            return f"- {self.capability_id} (available): {actions_str}{limit_str}"
        elif self.status == CapabilityStatus.NOT_CONFIGURED or not self.provider_configured:
            prov_hint = f" ({self.provider})" if self.provider else ""
            return f"- {self.capability_id} (not configured): {actions_str}; no provider{prov_hint} currently connected"
        elif self.status == CapabilityStatus.DISABLED:
            return f"- {self.capability_id} (disabled): explicitly disabled"
        else:
            return f"- {self.capability_id} (unsupported)"


class CapabilityRegistry:
    """Deterministic runtime registry of Mamba capabilities."""

    def __init__(self, descriptors: Sequence[CapabilityDescriptor] | None = None) -> None:
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        if descriptors:
            for d in descriptors:
                self.register(d)

    def register(self, descriptor: CapabilityDescriptor, *, overwrite: bool = True) -> None:
        """Register or update a capability descriptor."""
        key = descriptor.capability_id.strip().lower()
        if not overwrite and key in self._capabilities:
            raise ValueError(f"Capability '{descriptor.capability_id}' is already registered")
        self._capabilities[key] = descriptor

    def unregister(self, capability_id: str) -> bool:
        """Unregister a capability. Returns True if removed."""
        return self._capabilities.pop(capability_id.strip().lower(), None) is not None

    def get_capability(self, capability_id: str) -> CapabilityDescriptor | None:
        """Retrieve descriptor by capability ID."""
        return self._capabilities.get(capability_id.strip().lower())

    def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        """Return all registered capabilities."""
        return tuple(self._capabilities.values())

    def is_available(self, capability_id: str) -> bool:
        """Return True if capability is registered, enabled, and configured."""
        cap = self._capabilities.get(capability_id.strip().lower())
        return cap.is_available if cap else False

    def get_status(self, capability_id: str) -> CapabilityStatus:
        """Return availability status for capability ID, or UNSUPPORTED if not registered."""
        cap = self._capabilities.get(capability_id.strip().lower())
        if cap is None:
            return CapabilityStatus.UNSUPPORTED
        if not cap.provider_configured and cap.status == CapabilityStatus.AVAILABLE:
            return CapabilityStatus.NOT_CONFIGURED
        return cap.status

    def get_supported_actions(self, capability_id: str) -> tuple[str, ...]:
        """Return tuple of supported action names for a capability."""
        cap = self._capabilities.get(capability_id.strip().lower())
        return cap.supported_actions if cap else ()

    def find_capability_for_action(self, action: str) -> CapabilityDescriptor | None:
        """Find the capability descriptor that provides the specified action intent."""
        clean_action = action.strip().lower()
        # Direct match in supported actions
        for cap in self._capabilities.values():
            if clean_action in (a.lower() for a in cap.supported_actions):
                return cap
        # Direct capability ID match (e.g. intent="email")
        if clean_action in self._capabilities:
            return self._capabilities[clean_action]
        # Prefix or namespace match (e.g. intent="email.send" -> "email", "email_send" -> "email")
        for cap_id, cap in self._capabilities.items():
            if clean_action.startswith(f"{cap_id}_") or clean_action.startswith(f"{cap_id}."):
                return cap
        return None

    def set_status(
        self,
        capability_id: str,
        status: CapabilityStatus,
        *,
        provider: str | None = None,
        provider_configured: bool | None = None,
    ) -> None:
        """Update the status and provider configuration of an existing capability."""
        key = capability_id.strip().lower()
        existing = self._capabilities.get(key)
        if existing is None:
            raise KeyError(f"Capability '{capability_id}' is not registered")

        new_provider = provider if provider is not None else existing.provider
        new_configured = (
            provider_configured
            if provider_configured is not None
            else (False if status == CapabilityStatus.NOT_CONFIGURED else existing.provider_configured)
        )
        updated = CapabilityDescriptor(
            capability_id=existing.capability_id,
            name=existing.name,
            description=existing.description,
            supported_actions=existing.supported_actions,
            limitations=existing.limitations,
            unavailable_actions=existing.unavailable_actions,
            status=status,
            provider=new_provider,
            provider_configured=new_configured,
            metadata=dict(existing.metadata),
        )
        self._capabilities[key] = updated

    def format_summary_for_planner(self) -> str:
        """Produce a compact, structured capability summary for planner context."""
        lines = ["AVAILABLE CAPABILITIES:"]
        for cap in self._capabilities.values():
            lines.append(cap.format_summary_line())
        return "\n".join(lines)

    def format_system_context(self) -> str:
        """Produce concise instructions grounding Mamba in real tool capabilities."""
        lines = [
            "MAMBA RUNTIME CAPABILITIES & LIMITATIONS:",
            "You are Mamba, an operating layer between the user and digital tools, NOT a passive text-only chatbot.",
            "You have real, working tools and skills. When asked what you can do:",
            "- Acknowledge your real capabilities: you CAN open applications and websites, read/write files, run commands, inspect GitHub, search the web, manage calendar/email/messages, inspect screen/OCR, and remember user context.",
            "- State real limitations accurately: you CAN open URLs in the default browser, but you CANNOT control in-page media playback (e.g. playing/selecting specific YouTube videos) or click arbitrary web page buttons without dedicated extensions.",
            "- If a capability is not configured (e.g. email with no provider connected): state clearly: 'I have email capabilities, but no email provider is currently configured.' Do not claim email is impossible.",
            "- If a capability is genuinely unsupported: state clearly: 'I don't currently have a capability for that.' Never claim you are just a text model that cannot interact with external tools.",
            "",
            "Status of Capabilities:",
        ]
        for cap in self._capabilities.values():
            status_desc = "Ready" if cap.is_available else f"Not Available ({cap.status.value})"
            prov_desc = f" [Provider: {cap.provider}]" if cap.provider else ""
            limit_desc = f" - Limits: {'; '.join(cap.limitations)}" if cap.limitations else ""
            lines.append(f"- {cap.name} ({cap.capability_id}): {status_desc}{prov_desc}{limit_desc}")
        return "\n".join(lines)


def default_capability_registry(
    *,
    email_configured: bool = True,
    calendar_configured: bool = True,
    messaging_configured: bool = True,
    web_configured: bool | None = None,
    github_configured: bool | None = None,
) -> CapabilityRegistry:
    """Create a CapabilityRegistry populated with all 12 standard Mamba capabilities."""
    if web_configured is None:
        web_configured = bool(os.environ.get("TAVILY_API_KEY", "").strip())
    if github_configured is None:
        github_configured = bool(os.environ.get("GITHUB_TOKEN", "").strip())

    descriptors = (
        CapabilityDescriptor(
            capability_id="filesystem",
            name="Filesystem",
            description="Manage files and directories on local disk.",
            supported_actions=(
                "create_file",
                "read_file",
                "write_file",
                "delete_file",
                "list_directory",
                "create_directory",
            ),
            limitations=("Destructive operations (delete, overwrite) require user permission",),
            unavailable_actions=("Remote filesystem mounting without local OS path",),
            status=CapabilityStatus.AVAILABLE,
            provider="local",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="terminal",
            name="Terminal & Shell",
            description="Execute shell commands and scripts.",
            supported_actions=("run_command", "execute_command"),
            limitations=("Mutating and high-risk shell commands require user confirmation", "Interactive TUI/REPL input is not supported"),
            unavailable_actions=("Direct GUI automation via terminal",),
            status=CapabilityStatus.AVAILABLE,
            provider="local",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="desktop",
            name="Desktop & Windows",
            description="Launch applications, open URLs in browser, focus windows, and manage clipboard.",
            supported_actions=(
                "open_url",
                "open_application",
                "close_application",
                "focus_window",
                "get_foreground_window",
                "get_window_title",
                "find_window",
                "read_clipboard",
                "write_clipboard",
                "clear_clipboard",
            ),
            limitations=(
                "Can launch applications and open URLs in browser; cannot interact with in-page media controls like playing YouTube videos or click arbitrary web page buttons",
            ),
            unavailable_actions=("In-page video playback", "Arbitrary web button clicking", "Mouse cursor dragging"),
            status=CapabilityStatus.AVAILABLE,
            provider="local",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="system",
            name="System Information",
            description="Inspect system hardware, OS, CPU, memory, and GPU.",
            supported_actions=("system_info", "gpu_info", "platform_info", "memory_info"),
            limitations=("Read-only diagnostic reporting",),
            status=CapabilityStatus.AVAILABLE,
            provider="local",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="screen",
            name="Screen & Vision",
            description="Capture screen/regions, OCR text, and analyze visual layouts.",
            supported_actions=(
                "screenshot",
                "region_screenshot",
                "ocr",
                "region_ocr",
                "visual_understanding",
            ),
            limitations=("Screen inspection and OCR only; cannot click or drive GUI elements directly",),
            unavailable_actions=("Direct mouse clicking", "GUI input manipulation"),
            status=CapabilityStatus.AVAILABLE,
            provider="local",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="web",
            name="Web Search",
            description="Perform live web searches for up-to-date information.",
            supported_actions=("web_search",),
            limitations=("Requires Tavily API key for live search; read-only search results",),
            unavailable_actions=("Authenticated web scraping", "Form submission automation"),
            status=CapabilityStatus.AVAILABLE if web_configured else CapabilityStatus.NOT_CONFIGURED,
            provider="tavily",
            provider_configured=web_configured,
        ),
        CapabilityDescriptor(
            capability_id="github",
            name="GitHub & Git",
            description="Inspect repositories, issues, PRs, and perform Git operations.",
            supported_actions=(
                "get_repository",
                "read_file",
                "list_directory",
                "get_issue",
                "list_issues",
                "get_pull_request",
                "list_pull_requests",
                "search_code",
            ),
            limitations=("Destructive Git operations require user approval",),
            status=CapabilityStatus.AVAILABLE,
            provider="github_api" if github_configured else "unauthenticated",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="memory",
            name="Long-Term Memory",
            description="Store and recall user preferences, facts, and past context.",
            supported_actions=("remember", "recall", "delete_memory"),
            limitations=("Structured goal and fact storage; not an uncontrolled vector crawler",),
            status=CapabilityStatus.AVAILABLE,
            provider="sqlite",
            provider_configured=True,
        ),
        CapabilityDescriptor(
            capability_id="email",
            name="Email",
            description="Search, read, draft, and send emails.",
            supported_actions=(
                "search_emails",
                "list_emails",
                "read_email",
                "summarize_email",
                "draft_email",
                "send_email",
                "reply_email",
            ),
            limitations=("Sending and replying require explicit user approval; requires connected email provider",),
            status=CapabilityStatus.AVAILABLE if email_configured else CapabilityStatus.NOT_CONFIGURED,
            provider="simulated" if email_configured else None,
            provider_configured=email_configured,
        ),
        CapabilityDescriptor(
            capability_id="calendar",
            name="Calendar & Scheduling",
            description="Check schedule, detect conflicts, create, modify, and cancel events.",
            supported_actions=(
                "list_events",
                "search_events",
                "get_event",
                "check_conflicts",
                "create_event",
                "modify_event",
                "cancel_event",
            ),
            limitations=("Creating, modifying, and cancelling events require explicit user approval",),
            status=CapabilityStatus.AVAILABLE if calendar_configured else CapabilityStatus.NOT_CONFIGURED,
            provider="simulated" if calendar_configured else None,
            provider_configured=calendar_configured,
        ),
        CapabilityDescriptor(
            capability_id="messaging",
            name="Messaging & Chat",
            description="List conversations, search chats, read messages, draft, and send messages.",
            supported_actions=(
                "list_conversations",
                "search_conversations",
                "read_messages",
                "draft_message",
                "send_message",
                "reply_message",
            ),
            limitations=("Sending messages requires explicit user approval",),
            status=CapabilityStatus.AVAILABLE if messaging_configured else CapabilityStatus.NOT_CONFIGURED,
            provider="simulated" if messaging_configured else None,
            provider_configured=messaging_configured,
        ),
        CapabilityDescriptor(
            capability_id="analyze",
            name="Analytical Reasoning & Response",
            description="Reason, calculate, analyze data, and synthesize final user responses.",
            supported_actions=(
                "analyze",
                "calculate",
                "reason",
                "clarify",
                "respond",
                "summarize",
                "explain",
            ),
            limitations=("Pure model reasoning and synthesis; does not directly execute environment mutations",),
            status=CapabilityStatus.AVAILABLE,
            provider="model_router",
            provider_configured=True,
        ),
    )

    return CapabilityRegistry(descriptors)
