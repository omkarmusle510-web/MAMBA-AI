"""Mamba Brain - Intelligent observation-driven execution coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import time
from typing import Any

from memory.protocols import MemoryStore
from memory.types import MemoryEntry, MemoryQuery
from models.protocols import ModelProvider, ModelRouter
from models.types import ModelRequest
from permissions.policy import DefaultPermissionPolicy
from permissions.protocols import PermissionPolicy
from permissions.types import PermissionDecision, PermissionRequest, RiskLevel
from tasks.types import TaskInput
from verification.protocols import Verifier
from verification.types import UNAVAILABLE, VerificationRequest, VerificationResult, VerificationStatus
from verification.verifier import DefaultVerifier

from .capabilities import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityStatus,
    default_capability_registry,
)
from .cancellation import (
    MambaCancelledError,
    raise_if_cancelled,
    reset_current_token,
    set_current_token,
)
from . import timing
from .context import ExecutionContext
from .errors import CoreError
from .protocols import Executor, Planner
from .state import ExecutionState
from .streaming import reset_current_sink, set_current_sink
from .types import (
    ExecutionPlan,
    ExecutionResult,
    Observation,
    PlanStep,
    ResultStatus,
    UserRequest,
)

_DEFAULT_MAX_CYCLES = 10

# ── Cross-application target binding & outcome verification ─────────────────
# Intents that act on a supported application window. The application itself is
# named in the step metadata and resolved through the desktop capability's
# adapter registry, so this stays application-agnostic: adding an application
# adapter does not change Core.
_CROSS_APP_LAUNCH_INTENTS: frozenset[str] = frozenset(
    {
        "launch_application",
        "open_application",
        "start_application",
        "activate_application",
        # Notepad-flavoured aliases (thin adapters, not special cases)
        "launch_notepad",
        "open_notepad",
        "start_notepad",
    }
)
_CROSS_APP_TEXT_INTENTS: frozenset[str] = frozenset(
    {
        "type_text",
        "enter_text",
        "type_text_in_application",
        "write_in_application",
        # Notepad-flavoured aliases
        "type_text_in_notepad",
        "type_in_notepad",
        "type_into_notepad",
        "write_in_notepad",
    }
)
_CROSS_APP_INSPECT_INTENTS: frozenset[str] = frozenset(
    {
        "inspect_applications",
        "list_applications",
        "supported_applications",
        "find_application",
    }
)
_CROSS_APP_INTENTS: frozenset[str] = (
    _CROSS_APP_LAUNCH_INTENTS | _CROSS_APP_TEXT_INTENTS | _CROSS_APP_INSPECT_INTENTS
)

# Read intents that observe an application's content, in preference order.
_CROSS_APP_READ_INTENTS: tuple[str, ...] = (
    "read_application_text",
    "read_notepad_text",
)

# Close intents act on whatever window an earlier step bound. They are not
# launch/type/read intents, but they must inherit the exact bound target the
# same way, so the close cannot drift onto another matching window.
_CROSS_APP_CLOSE_INTENTS: frozenset[str] = frozenset(
    {
        "close_window",
        "terminate_window",
        "kill_window",
        "destroy_window",
    }
)

# Acting intents whose target, when an earlier step in the same execution
# bound one, must be that exact window rather than a fresh resolution by
# application name (which could silently pick another matching window).
_CROSS_APP_CARRY_INTENTS: frozenset[str] = (
    _CROSS_APP_TEXT_INTENTS
    | frozenset(_CROSS_APP_READ_INTENTS)
    | _CROSS_APP_CLOSE_INTENTS
)

_UI_TEXT_PREDICATE_KEY = "ui_text_contains"
"""Expected-predicate that verifies an outcome by observing the target's UI.

The payload is ``{"app": "<adapter id>", "text": "<expected text>"}``; verification
reads the text back through the desktop capability's own observation mechanism
and hands it to the existing verifier as a ``contains`` comparison.
"""

_NOTEPAD_APP = "Notepad"
"""The only application whose intents may omit an explicit application name."""


def _step_text_argument(step: PlanStep) -> str:
    """Extract the text a step intends to type, from planner metadata."""
    for key in ("text", "content", "value", "input"):
        value = step.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _step_app_argument(step: PlanStep) -> str:
    """Extract the application a step targets, from planner metadata."""
    for key in ("app_id", "app", "application", "target_app", "program"):
        value = step.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_NOTEPAD_FLAVOURED_INTENTS: frozenset[str] = frozenset(
    {
        "launch_notepad",
        "open_notepad",
        "start_notepad",
        "type_text_in_notepad",
        "type_in_notepad",
        "type_into_notepad",
        "write_in_notepad",
        "read_notepad_text",
        "notepad_text",
        "get_notepad_text",
    }
)

# ── Browser interaction ────────────────────────────────────────────────────
# Intents that drive a real browser page. The action for the step travels in its
# metadata; these sets only tell Core which steps are browser interactions, so it
# can ask for the right outcome observation and verify it through the existing
# verifier. No browser-specific reasoning lives in Core.
_BROWSER_NAVIGATE_INTENTS: frozenset[str] = frozenset(
    {
        "open_url_in_browser",
        "open_page",
        "browse_url",
        "visit_page",
        "navigate_browser",
        "browser_navigate",
        "go_to_url",
        "browser_back",
        "go_back",
        "navigate_back",
        "browser_forward",
        "go_forward",
        "navigate_forward",
        "browser_reload",
        "reload_page",
        "refresh_page",
    }
)
_BROWSER_MUTATING_INTENTS: frozenset[str] = frozenset(
    {
        "click_element",
        "browser_click",
        "click_on_page",
        "click_link",
        "type_text_in_page",
        "browser_type",
        "fill_field",
        "enter_text_in_page",
        "clear_field",
        "browser_clear",
        "clear_input",
        "press_key_in_page",
        "browser_press_key",
        "page_press_key",
        "browser_scroll",
        "scroll_page",
        "select_option",
        "browser_select",
        "choose_option",
    }
)
_BROWSER_READ_INTENTS: frozenset[str] = frozenset(
    {
        "browser_current",
        "get_current_page",
        "current_url",
        "inspect_page",
        "browser_inspect",
        "browser_inspect_page",
        "page_snapshot",
        "browser_snapshot",
        "read_page",
        "browser_read_page",
        "page_text",
        "read_webpage",
        "browser_links",
        "page_links",
        "list_page_links",
        "browser_buttons",
        "page_buttons",
        "browser_fields",
        "page_fields",
        "form_fields",
        "find_on_page",
        "browser_find",
        "search_page",
        "browser_wait",
        "wait_for_page",
        "wait_for_text",
        "list_browser_targets",
        "browser_targets",
        "list_browser_tabs",
        "browser_tabs",
        "attach_browser",
        "bind_browser",
        "select_browser_tab",
    }
)
_BROWSER_INTENTS: frozenset[str] = (
    _BROWSER_NAVIGATE_INTENTS | _BROWSER_MUTATING_INTENTS | _BROWSER_READ_INTENTS
)

_BROWSER_EXPECTED_KEYS: tuple[str, ...] = (
    "expected",
    "verify",
    "expect_text",
    "expect_url",
    "expect_title",
)


def _browser_expectation(step: PlanStep) -> dict[str, Any] | None:
    """Build the expected-outcome predicate for a browser step.

    A step states what should be observable after the action (this is what makes
    "clicked Search" different from "search results appeared"):

    * ``expected`` / ``expect_text`` — text that must appear in the page;
    * ``expect_url`` — a URL (or fragment) the page must be on;
    * ``expect_title`` — text the page title must contain.

    Returns ``None`` when the step states nothing to check, in which case the
    outcome is reported as executed-but-unverified instead of assumed successful.
    """
    metadata = step.metadata
    raw_expected = metadata.get("expected")

    predicate: dict[str, Any] = {}
    if isinstance(raw_expected, dict):
        for key in ("url_contains", "url", "expect_url"):
            if raw_expected.get(key):
                predicate["url_contains"] = str(raw_expected[key])
        for key in ("title_contains", "title", "expect_title"):
            if raw_expected.get(key):
                predicate["title_contains"] = str(raw_expected[key])
        for key in ("contains", "text", "expect_text"):
            if raw_expected.get(key):
                predicate["contains"] = str(raw_expected[key])
        if predicate:
            return predicate
        return None

    if isinstance(raw_expected, str) and raw_expected.strip():
        predicate["contains"] = raw_expected.strip()
    for key, target in (
        ("expect_text", "contains"),
        ("expect_url", "url_contains"),
        ("expect_title", "title_contains"),
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            predicate[target] = value.strip()

    verify = metadata.get("verify")
    if not predicate and isinstance(verify, str) and verify.strip():
        predicate["contains"] = verify.strip()
    return predicate or None

_NEEDS_REPLANNING_KEY = "needs_replanning"
"""ExecutionPlan.metadata key a planner sets to request another reasoning
cycle after this plan's steps run, because it already expects to need
information those steps will produce. Absent/false means the plan is
expected to satisfy the goal on its own - no extra step or model call is
required for the normal case.
"""


def _last_observation_content(context: ExecutionContext) -> str | None:
    observations = context.observations
    if not observations:
        return None
    # Trailing "outcome verified" notes are confirmations of the step that just
    # ran, not a new result: the step's own observation stays the reported output.
    for observation in reversed(observations):
        if observation.metadata.get("action") in ("verification_passed", "verification_unverified"):
            continue
        return observation.content
    return observations[-1].content


def _plan_needs_replanning(plan: ExecutionPlan) -> bool:
    return plan.metadata.get(_NEEDS_REPLANNING_KEY) is True


def _step_signature(step: PlanStep) -> tuple[str, str, str]:
    """A fingerprint used to identify a step (intent, description, target/path/command)."""
    target = str(
        step.metadata.get("path")
        or step.metadata.get("file")
        or step.metadata.get("command")
        or step.metadata.get("url")
        or step.metadata.get("query")
        or step.metadata.get("text")
        or ""
    ).strip().lower()
    app = str(
        step.metadata.get("app")
        or step.metadata.get("application")
        or step.metadata.get("target_app")
        or ""
    ).strip().lower()
    return (step.intent.strip().lower(), step.description.strip().lower(), f"{app}|{target}")


_APPROVAL_TTL_SECONDS: float = 120.0
"""How long a paused approval remains valid before it is discarded."""


def _approval_signature(step: PlanStep) -> tuple[str, str, str, str]:
    """The exact action + bound target an approval is granted for.

    Extends the step fingerprint with the pinned window identity (handle and
    owning PID) when one is bound, so an approval given for one target can
    never authorize execution against a different one.
    """
    base = _step_signature(step)
    hwnd = str(step.metadata.get("hwnd") or "").strip()
    pid = str(step.metadata.get("target_pid") or "").strip()
    return (*base, f"{hwnd}|{pid}")


def _plan_signature(plan: ExecutionPlan) -> tuple[tuple[str, str, str], ...]:
    """A fingerprint used to detect a planner repeating an identical plan."""
    return tuple(_step_signature(step) for step in plan.steps)


_APPROVAL_PHRASES: frozenset[str] = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "ok",
        "okay",
        "approved",
        "approve",
        "go ahead",
        "do it",
        "proceed",
        "confirm",
        "confirmed",
        "please do",
        "execute",
        "run it",
        "that's fine",
        "that is fine",
        "i approve",
        "affirmative",
    }
)

_DENIAL_PHRASES: frozenset[str] = frozenset(
    {
        "no",
        "nope",
        "nah",
        "cancel",
        "don't",
        "dont",
        "stop",
        "never mind",
        "nevermind",
        "deny",
        "denied",
        "reject",
        "abort",
        "halt",
        "do not",
    }
)

_EXTENDED_APPROVAL_PATTERNS = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok|okay|approved?|i\s+approve|proceed|confirm(?:ed)?|please\s+do|execute|run\s+it|do\s+it|send\s+it)"
    r"(?:[,.\s]+(?:please|you\s+can\s+proceed|proceed|do\s+it|run\s+that|do\s+that|go\s+ahead|and\s+do\s+it|and\s+run\s+it|send\s+it|send\s+the\s+email|send\s+the\s+message|send\s+this|create\s+it|update\s+it))*[.!?,]*$"
    r"|^(?:go\s+ahead(?:\s+and\s+(?:do|run|send|create)\s+it)?|do\s+that|run\s+that|execute\s+that|send\s+it|send\s+the\s+email|send\s+the\s+message|please\s+send\s+it|please\s+proceed|you\s+can\s+proceed|sounds\s+good|that'?s\s+fine|that\s+is\s+fine)[.!?,]*$",
    re.IGNORECASE,
)

_EXTENDED_DENIAL_PATTERNS = re.compile(
    r"^(?:no|nope|nah|cancel|don'?t|stop|never\s*mind|deny|denied|reject|abort|halt|do\s+not)"
    r"(?:\s+(?:it|that|do\s+it|do\s+that|thanks|please))*[.!?,]*$"
    r"|^(?:forget\s+it|don'?t\s+do\s+(?:it|that))[.!?,]*$",
    re.IGNORECASE,
)

_REPLACEMENT_PATTERN = re.compile(
    r"^(?:no[,.\s]+)?(?:don'?t(?:\s+do\s+(?:that|it))?|cancel(?:\s+that|\s+it)?|stop)[,.\s]+(?:instead[,.\s]*|rather[,.\s]*|please[,.\s]*)*(?P<replacement>.+)$",
    re.IGNORECASE,
)

_FILENAME_REPLACEMENT_PATTERN = re.compile(
    r"^(?:actually\s+)?(?:make\s+that|change\s+(?:that|it|the\s+file)\s+to)\s+(?P<new_name>[^\s]+)[.!?,]*$",
    re.IGNORECASE,
)


def _is_approval_phrase(text: str) -> bool:
    clean = text.strip().lower().rstrip(".!?,")
    if clean in _APPROVAL_PHRASES:
        return True
    return bool(_EXTENDED_APPROVAL_PATTERNS.match(clean))


def _is_denial_phrase(text: str) -> bool:
    clean = text.strip().lower().rstrip(".!?,")
    if clean in _DENIAL_PHRASES:
        return True
    return bool(_EXTENDED_DENIAL_PATTERNS.match(clean))


_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_SENSITIVE_METADATA_KEYS: tuple[str, ...] = (
    "destructive",
    "user_sensitive",
    "irreversible",
    "externally_visible",
)
"""Escalation flags owned by the capability table, never by a plan."""


def _coerce_risk_level(value: Any) -> RiskLevel:
    """Normalize a risk value, treating anything unrecognised as LOW."""
    if isinstance(value, RiskLevel):
        return value
    if isinstance(value, str):
        try:
            return RiskLevel(value.lower())
        except ValueError:
            return RiskLevel.LOW
    return RiskLevel.LOW


def _max_risk_level(first: RiskLevel, second: RiskLevel) -> RiskLevel:
    """The stricter of two classifications; claims can raise risk, never lower it."""
    return first if _RISK_ORDER[first] >= _RISK_ORDER[second] else second


@dataclass(slots=True)
class PendingApproval:
    """Represents a paused step waiting for explicit user confirmation."""

    step: PlanStep
    context: ExecutionContext
    plan: ExecutionPlan
    step_index: int
    user_request: UserRequest
    reason: str
    signature: tuple[str, str, str, str]
    """The action + bound target the user is being asked about, captured when
    the step paused. A later execution is only covered when it matches this."""
    created_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class Brain:
    """Observation-driven intelligent execution coordinator for Mamba.

    Coordinates Mamba components around a user's goal through a bounded
    decision/execution loop where each observation can influence what
    happens next:

    Request -> Context + Memory -> Reason/Plan -> Choose Next Action
    -> Permission -> Execute -> Observe -> Evaluate Observation
    -> Continue / Re-plan / Finish -> Verify -> Memory -> Response
    """

    planner: Planner
    executor: Executor
    memory: MemoryStore | None = None
    permissions: PermissionPolicy | None = None
    verifier: Verifier | None = None
    model_router: ModelRouter | None = None
    max_cycles: int = _DEFAULT_MAX_CYCLES
    capabilities: CapabilityRegistry | None = None
    memory_manager: Any = None
    _pending_approval: PendingApproval | None = field(default=None, init=False)
    _last_turn_context: dict[str, Any] | None = field(default=None, init=False)
    _active_entities: dict[str, str] = field(default_factory=dict, init=False)
    _memory_manager: Any = field(default=None, init=False)
    _approved_step_ids: set[str] = field(default_factory=set, init=False)
    """Step ids the user has explicitly approved this session.

    The only accepted record of approval. Ids are generated by PlanStep, never
    supplied by a plan, so this cannot be forged or guessed from model output.
    """

    _approved_signatures: set[tuple[str, str, str, str]] = field(
        default_factory=set, init=False
    )
    """The action + bound target each recorded approval was granted for.

    An id alone is not enough: the executing step must still carry the same
    fingerprint — including its pinned window identity — or it is re-asked."""

    def __post_init__(self) -> None:
        if self.permissions is None:
            self.permissions = DefaultPermissionPolicy()
        if self.verifier is None:
            self.verifier = DefaultVerifier()
        if self.capabilities is None:
            self.capabilities = default_capability_registry()
        self._pending_approval = None
        self._last_turn_context = None
        self._active_entities = {}
        self._approved_step_ids = set()
        self._approved_signatures = set()

        # Memory Manager initialization
        if self.memory_manager is not None:
            self._memory_manager = self.memory_manager
            if self.memory is None and hasattr(self.memory_manager, "store"):
                self.memory = self.memory_manager.store
        elif self.memory is not None:
            if hasattr(self.memory, "remember") and hasattr(self.memory, "retrieve"):
                self._memory_manager = self.memory
                if hasattr(self.memory, "store"):
                    self.memory = self.memory.store
            else:
                try:
                    from memory.embedding import SentenceTransformerEmbeddingProvider
                    from memory.manager import MemoryManager

                    self._memory_manager = MemoryManager(
                        store=self.memory,
                        embedding_provider=SentenceTransformerEmbeddingProvider(),
                        model_router=self.model_router,
                    )
                except Exception:
                    self._memory_manager = None
        else:
            self._memory_manager = None

    @property
    def active_memory_manager(self) -> Any:
        """Return the active MemoryManager instance if configured."""
        return self._memory_manager

    def is_capability_available(self, capability_id: str) -> bool:
        """Check if a capability is available and configured at runtime."""
        return self.capabilities.is_available(capability_id) if self.capabilities else False

    def get_capability(self, capability_id: str) -> CapabilityDescriptor | None:
        """Retrieve capability descriptor."""
        return self.capabilities.get_capability(capability_id) if self.capabilities else None

    def run(
        self,
        request: str | UserRequest,
        *,
        on_progress: Any = None,
        cancel_token: Any = None,
        stream_sink: Any = None,
    ) -> ExecutionResult:
        """Run a user request through the observation-driven execution lifecycle.

        Args:
            request: User goal as string or UserRequest.
            on_progress: Optional callable(str) receiving lightweight
                milestone updates (e.g. "Planning...", "Executing...").
            cancel_token: Optional cooperative CancellationToken. When supplied
                it is bound as the ambient token for this execution so the loop
                and interruptible collaborators (e.g. the MCP stdio client) can
                honour cancellation. Omitting it preserves prior behaviour.
            stream_sink: Optional callable(str) receiving provisional model text
                deltas. Like ``cancel_token`` it is bound as the ambient sink for
                this execution, so the answer-producing skill can stream without
                any signature change. It never carries authority: observations,
                verification, memory and the returned ``ExecutionResult`` all use
                the complete text. Omitting it preserves prior behaviour.
        """
        _progress = on_progress if callable(on_progress) else None
        _sink = stream_sink if callable(stream_sink) else None
        _reset = set_current_token(cancel_token) if cancel_token is not None else None
        _sink_reset = (
            set_current_sink(_sink) if _sink is not None else None
        )
        try:
            return self._run(request, _progress)
        finally:
            if _sink_reset is not None:
                reset_current_sink(_sink_reset)
            if _reset is not None:
                reset_current_token(_reset)

    def _run(self, request: str | UserRequest, _progress: Any) -> ExecutionResult:
        """Body of :meth:`run`; the ambient cancel token is already bound."""
        _t_start = time.perf_counter()
        holder: dict[str, Any] = {}
        try:
            return self._run_impl(request, _progress, holder)
        finally:
            sink = holder.get("md")
            if sink is not None:
                timing.mark(sink, "request_total", time.perf_counter() - _t_start)

    def _run_impl(
        self, request: str | UserRequest, _progress: Any, holder: dict[str, Any],
    ) -> ExecutionResult:
        # ── 1. Request Intake ──
        _t_intake = time.perf_counter()
        user_request = self._intake(request)
        if user_request is None:
            return ExecutionResult(
                execution_id="",
                status=ResultStatus.FAILED,
                goal=request if isinstance(request, str) else str(request),
                observations=(),
                error="goal must not be empty"
                if isinstance(request, str) and not request.strip()
                else f"expected str or UserRequest, got {type(request).__name__}",
            )
        holder["md"] = user_request.metadata
        timing.mark(user_request.metadata, "intake", time.perf_counter() - _t_intake)

        # ── 1a. Capability Awareness Context ──
        if self.capabilities is not None:
            user_request.metadata["capabilities"] = self.capabilities.format_summary_for_planner()
            user_request.metadata["capability_context"] = self.capabilities.format_system_context()

        # ── 1b. Prior Turn Context & Active Entities Propagation ──
        if self._last_turn_context and "prior_turn" not in user_request.metadata:
            user_request.metadata["prior_turn"] = self._last_turn_context
        if self._active_entities and "active_entities" not in user_request.metadata:
            user_request.metadata["active_entities"] = dict(self._active_entities)

        # ── 1c. Correction & Replacement Context ──
        rep_match = _REPLACEMENT_PATTERN.match(user_request.goal)
        if rep_match:
            if self._pending_approval is not None:
                self._pending_approval = None
                self._clear_approvals()
            replacement_goal = rep_match.group("replacement").strip()
            user_request = replace(user_request, goal=replacement_goal)

        fn_match = _FILENAME_REPLACEMENT_PATTERN.match(user_request.goal)
        if fn_match:
            new_name = fn_match.group("new_name").strip()
            self._active_entities["file"] = new_name
            if self._pending_approval is not None:
                pending = self._pending_approval
                self._pending_approval = None
                pending.step.metadata["path"] = new_name
                pending.step.description = re.sub(r'(\b\S+\.[a-zA-Z0-9]+\b)', new_name, pending.step.description)
                res = self._resume_pending_approval(pending, user_request, on_progress=_progress)
                self._record_turn_context(user_request, res)
                return res
            elif self._last_turn_context:
                prev_goal = self._last_turn_context.get("goal", "")
                if prev_goal:
                    user_request = replace(
                        user_request,
                        goal=re.sub(r'(\b\S+\.[a-zA-Z0-9]+\b)', new_name, prev_goal),
                    )

        # ── 1d. Pending Approval Resolution ──
        if _is_approval_phrase(user_request.goal):
            if self._pending_approval is None:
                context = ExecutionContext.from_request(user_request)
                context.transition_to(ExecutionState.PLANNING)
                msg = "There is no pending action requiring approval. Please specify what you would like me to do."
                plan = ExecutionPlan(steps=(PlanStep(description="Respond to user", intent="respond"),))
                context.attach_plan(plan)
                context.transition_to(ExecutionState.EXECUTING)
                obs = Observation(
                    step_id=plan.steps[0].id,
                    content=msg,
                    success=True,
                )
                context.add_observation(obs)
                context.transition_to(ExecutionState.COMPLETED)
                res = context.record.to_result(output=msg)
                self._record_turn_context(user_request, res)
                return res
            elif user_request.metadata.get("input_modality") == "voice":
                # SAFETY (voice approval policy): a pending approval is always a
                # HIGH-risk action (LOW/MEDIUM never ASK). Voice-originated
                # approvals are never honored — the user must confirm visually
                # (SudoPopup click) or by typed approval. The pending approval
                # is deliberately left intact so it can still be resolved.
                context = ExecutionContext.from_request(user_request)
                context.transition_to(ExecutionState.PLANNING)
                msg = (
                    f"Voice approval is not permitted for "
                    f"'{self._pending_approval.step.description}'. "
                    "Please confirm on screen to proceed."
                )
                plan = ExecutionPlan(steps=(PlanStep(description="Respond to user", intent="respond"),))
                context.attach_plan(plan)
                context.transition_to(ExecutionState.EXECUTING)
                obs = Observation(
                    step_id=plan.steps[0].id,
                    content=msg,
                    success=True,
                )
                context.add_observation(obs)
                context.transition_to(ExecutionState.COMPLETED)
                res = context.record.to_result(output=msg)
                self._record_turn_context(user_request, res)
                return res
            else:
                pending = self._pending_approval
                self._pending_approval = None
                res = self._resume_pending_approval(pending, user_request, on_progress=_progress)
                self._record_turn_context(user_request, res)
                return res

        if _is_denial_phrase(user_request.goal):
            if self._pending_approval is not None:
                pending = self._pending_approval
                self._pending_approval = None
                self._clear_approvals()
                context = ExecutionContext.from_request(user_request)
                context.transition_to(ExecutionState.PLANNING)
                msg = f"Operation '{pending.step.description}' was cancelled."
                plan = ExecutionPlan(steps=(PlanStep(description=f"Cancel '{pending.step.description}'", intent="cancel"),))
                context.attach_plan(plan)
                context.transition_to(ExecutionState.EXECUTING)
                obs = Observation(
                    step_id=plan.steps[0].id,
                    content=msg,
                    success=True,
                    metadata={"cancelled": True},
                )
                context.add_observation(obs)
                context.transition_to(ExecutionState.COMPLETED)
                res = context.record.to_result(output=msg)
                self._record_turn_context(user_request, res)
                return res

        # Clear stale pending approval on an unrelated request
        if self._pending_approval is not None:
            self._pending_approval = None
        # An unrelated request is not an approval of anything: drop prior
        # approvals so a later step cannot inherit one. Approval turns return
        # above, so this never runs on the turn that consumes an approval.
        self._clear_approvals()

        # ── 1e. Conversational Referent Resolution ──
        resolved_goal = self._resolve_referents(user_request.goal)
        if resolved_goal != user_request.goal:
            user_request = replace(user_request, goal=resolved_goal)
        user_request.metadata["active_entities"] = dict(self._active_entities)

        # ── 1f. Simple-request fast path (Parts 3/7) ──
        # Only confidently-simple, side-effect-free requests are dispatched here,
        # and they still run through _execution_loop/_execute_step (permission,
        # target binding, verification, cancellation, execution state all intact).
        # It skips only project discovery, pre-plan memory retrieval, and the
        # planning model call. Approval/correction/referent handling above still
        # take precedence.
        from .quickpath import build_fast_plan
        fast_plan = build_fast_plan(user_request)
        if fast_plan is not None:
            context = ExecutionContext.from_request(user_request)
            try:
                res = self._execution_loop(
                    context, user_request, on_progress=_progress, seed_plan=fast_plan,
                )
            except MambaCancelledError:
                res = self._cancel_result(context, _progress)
            else:
                if _progress and res.status == ResultStatus.COMPLETED:
                    _progress("Completed")
            self._record_turn_context(user_request, res)
            return res

        # ── 2. Context Assembly ──
        _t_ctx = time.perf_counter()
        context = ExecutionContext.from_request(user_request)
        timing.mark(user_request.metadata, "context_assembly", time.perf_counter() - _t_ctx)

        # ── 3. Memory Retrieval ──
        if _progress:
            _progress("Understanding...")
        if not self._retrieve_memory(context, user_request):
            return context.record.to_result()

        # ── 4. Observation-Driven Execution Loop ──
        try:
            res = self._execution_loop(context, user_request, on_progress=_progress)
        except MambaCancelledError:
            res = self._cancel_result(context, _progress)
        else:
            if _progress and res.status == ResultStatus.COMPLETED:
                _progress("Completed")
        self._record_turn_context(user_request, res)
        return res

    def _cancel_result(
        self, context: ExecutionContext, progress: Any,
    ) -> ExecutionResult:
        """Finalize a cancelled execution deterministically.

        A cancelled run is recorded as CANCELLED and can never be reported as
        completed/success. If the record already reached a terminal state the
        existing outcome is preserved rather than overwritten.
        """
        if context.record.state not in (
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        ):
            context.record.mark_cancelled()
        # Cancellation is also a revocation: no paused approval may survive it,
        # and no previously granted approval is carried into a later turn.
        self.revoke_pending_approval()
        if progress:
            progress("Cancelled")
        return context.record.to_result(output="Cancelled")

    def revoke_pending_approval(self) -> None:
        """Invalidate any paused approval and every prior grant.

        Safe to call at any time (an explicit cancel, a disconnect, or a
        cancellation that unwinds a turn): afterwards a stale approval can no
        longer be resumed, and no step can execute on an old grant.
        """
        self._pending_approval = None
        self._clear_approvals()

    def _clear_approvals(self) -> None:
        """Drop all recorded approvals — ids and their action+target signatures."""
        self._approved_step_ids.clear()
        self._approved_signatures.clear()

    def route_model(self, request: ModelRequest) -> ModelProvider | None:
        """Route a model request through the model router if configured."""
        if self.model_router is not None:
            return self.model_router.route(request)
        return None

    def shutdown(self) -> None:
        """Bounded, best-effort teardown of resources this Brain owns.

        Application shutdown is deliberately separate from per-request
        cancellation: this never touches a CancellationToken. It stops any
        browser provider (which terminates the MCP subprocess tree so no
        orphaned process survives) and closes the persistent memory store.
        Every step is independently guarded so teardown can neither hang nor
        raise.
        """
        handlers: list[Any] = []
        single = getattr(self.executor, "handler", None)
        if single is not None:
            handlers.append(single)
        mapping = getattr(self.executor, "handlers", None)
        if mapping:
            try:
                handlers.extend(mapping.values())
            except Exception:
                pass

        seen: set[int] = set()
        for handler in handlers:
            try:
                session = handler.browser_session
            except Exception:
                continue
            if session is None or id(session) in seen:
                continue
            seen.add(id(session))
            try:
                session.stop()
            except Exception:
                pass

        closed_stores: set[int] = set()
        for store in (self.memory, self._memory_manager):
            if store is None or id(store) in closed_stores:
                continue
            close = getattr(store, "close", None)
            if callable(close):
                closed_stores.add(id(store))
                try:
                    close()
                except Exception:
                    pass

    # ── Private Implementation ──

    def _resolve_referents(self, text: str) -> str:
        """Resolve anaphoric referents ('that file', 'the file', 'this repo', 'the one I just created', 'the folder', 'that command', 'it') against active entities."""
        resolved = text
        active_file = self._active_entities.get("file")
        active_folder = self._active_entities.get("folder")
        active_repo = self._active_entities.get("repository")
        active_command = self._active_entities.get("command")

        # 1. Resolve 'this repository', 'this repo', 'the repo'
        if active_repo:
            resolved = re.sub(
                r'\b(?:this|the|current)\s+(?:repository|repo)\b',
                active_repo,
                resolved,
                flags=re.IGNORECASE,
            )

        # 2. Resolve 'that file', 'the file', 'the previous file', 'this file', 'the one I just created'
        if active_file:
            resolved = re.sub(
                r'\b(?:that|the(?:\s+previous)?|this)\s+file\b',
                active_file,
                resolved,
                flags=re.IGNORECASE,
            )
            resolved = re.sub(
                r'\b(?:the\s+one\s+I\s+just\s+created|the\s+file\s+I\s+just\s+created)\b',
                active_file,
                resolved,
                flags=re.IGNORECASE,
            )
            # Resolve trailing 'it' in common actions like 'read it', 'delete it', 'open it'
            resolved = re.sub(
                r'\b(read|delete|remove|open|inspect|check|show)\s+it\b',
                rf'\1 {active_file}',
                resolved,
                flags=re.IGNORECASE,
            )

        # 3. Resolve 'the folder', 'that folder', 'the directory', 'that directory'
        if active_folder:
            resolved = re.sub(
                r'\b(?:that|the(?:\s+previous)?|this)\s+(?:folder|directory)\b',
                active_folder,
                resolved,
                flags=re.IGNORECASE,
            )

        # 4. Resolve 'the command you just ran', 'the previous command', 'that command'
        if active_command:
            resolved = re.sub(
                r'\b(?:the\s+command\s+you\s+just\s+ran|the\s+previous\s+command|that\s+command)\b',
                active_command,
                resolved,
                flags=re.IGNORECASE,
            )

        # 5. Resolve 'that email', 'the email', 'the latest email', 'the previous email'
        active_email = self._active_entities.get("email")
        if active_email:
            resolved = re.sub(
                r'\b(?:that|the(?:\s+latest|\s+previous)?|this)\s+email\b',
                active_email,
                resolved,
                flags=re.IGNORECASE,
            )

        # 6. Resolve 'that meeting', 'the meeting', 'the meeting tomorrow', 'that event'
        active_meeting = self._active_entities.get("meeting")
        if active_meeting:
            resolved = re.sub(
                r'\b(?:that|the(?:\s+previous)?|this)\s+(?:meeting|event)(?:\s+tomorrow)?\b',
                active_meeting,
                resolved,
                flags=re.IGNORECASE,
            )

        # 7. Resolve 'that conversation', 'the conversation', 'the message I just mentioned'
        active_conv = self._active_entities.get("conversation")
        if active_conv:
            resolved = re.sub(
                r'\b(?:that|the(?:\s+previous)?|this)\s+conversation\b',
                active_conv,
                resolved,
                flags=re.IGNORECASE,
            )
            resolved = re.sub(
                r'\bthe\s+message\s+I\s+just\s+mentioned\b',
                active_conv,
                resolved,
                flags=re.IGNORECASE,
            )

        return resolved

    def _record_turn_context(
        self, user_request: UserRequest, result: ExecutionResult
    ) -> None:
        """Cache the most recent turn context and active entities for natural follow-ups."""
        entities = dict(self._active_entities)

        # Extract entities from result observations
        for obs in result.observations:
            meta = obs.metadata
            if meta.get("path"):
                p = str(meta["path"])
                is_dir = not ("." in Path(p).name) or Path(p).is_dir() or "dir" in obs.content.lower()
                entities["folder" if is_dir else "file"] = p
            if meta.get("repo") or meta.get("repository"):
                repo_str = meta.get("repo") or meta.get("repository")
                entities["repository"] = str(repo_str)
            if meta.get("command"):
                cmd_val = meta["command"]
                if isinstance(cmd_val, dict):
                    exe = cmd_val.get("executable", "")
                    args = cmd_val.get("args", [])
                    cmd_str = f"{exe} {' '.join(str(a) for a in args)}".strip()
                    entities["command"] = cmd_str or str(exe)
                else:
                    entities["command"] = str(cmd_val)
            if meta.get("url"):
                entities["url"] = str(meta["url"])

            # Email entity extraction
            if meta.get("email_id"):
                entities["email"] = str(meta["email_id"])
            elif meta.get("subject"):
                entities["email"] = str(meta["subject"])
            elif isinstance(meta.get("result"), dict) and meta["result"].get("emails"):
                first_email = meta["result"]["emails"][0]
                entities["email"] = str(first_email.get("id") or first_email.get("subject"))

            # Calendar event/meeting entity extraction
            if meta.get("event_id"):
                entities["meeting"] = str(meta["event_id"])
            elif meta.get("title") and any(k in meta.get("action", "") for k in ("event", "calendar", "meeting")):
                entities["meeting"] = str(meta["title"])
            elif isinstance(meta.get("result"), dict) and meta["result"].get("events"):
                first_event = meta["result"]["events"][0]
                entities["meeting"] = str(first_event.get("id") or first_event.get("title"))

            # Messaging conversation entity extraction
            if meta.get("conversation_id"):
                entities["conversation"] = str(meta["conversation_id"])
            elif meta.get("recipient"):
                entities["conversation"] = str(meta["recipient"])
            elif isinstance(meta.get("result"), dict) and meta["result"].get("conversations"):
                first_conv = meta["result"]["conversations"][0]
                entities["conversation"] = str(first_conv.get("id") or first_conv.get("name"))

        # Also extract from user goal if filename or repo was explicitly mentioned
        goal_text = user_request.goal
        file_match = re.search(r'\b([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)\b', goal_text)
        if file_match and not file_match.group(1).startswith("http"):
            entities["file"] = file_match.group(1)

        repo_match = re.search(r'github\.com/([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+)', goal_text)
        if repo_match:
            entities["repository"] = repo_match.group(1).removesuffix(".git")

        self._active_entities = entities
        self._last_turn_context = {
            "goal": user_request.goal,
            "status": result.status.value,
            "output": result.output,
            "last_observation": (
                result.observations[-1].content if result.observations else None
            ),
            "active_entities": dict(entities),
        }

    def _intake(self, request: str | UserRequest) -> UserRequest | None:
        """Normalize and validate the incoming request."""
        if isinstance(request, str):
            if not request.strip():
                return None
            return UserRequest(goal=request.strip())
        if isinstance(request, UserRequest):
            return request
        return None

    def _project_context_needed(self, user_request: UserRequest) -> bool:
        """Conservative gate: retain project discovery unless confidently generic."""
        from .quickpath import is_clearly_project_irrelevant
        return not is_clearly_project_irrelevant(
            user_request.goal,
            has_active_repository=bool(self._active_entities.get("repository")),
            metadata=user_request.metadata,
        )

    def _memory_retrieval_wanted(self, user_request: UserRequest) -> bool:
        """Conservative gate: skip pre-plan memory retrieval only where it cannot
        change Mamba's expected behavior.

        Explicit recall always retrieves. Pure arithmetic never needs prior
        memories. Everything else retrieves (retain on uncertainty) so memory
        behavior is unchanged for genuinely ambiguous requests — Spec Part 6.
        """
        from .quickpath import is_arithmetic, is_memory_recall
        if is_memory_recall(user_request.goal):
            return True
        if is_arithmetic(user_request.goal):
            return False
        return True

    def _retrieve_memory(
        self, context: ExecutionContext, user_request: UserRequest,
    ) -> bool:
        """Attach project context and query memory store for relevant memories.

        Returns True if successful (or no memory configured), False on failure.
        """
        project = (
            user_request.metadata.get("project")
            or self._active_entities.get("repository")
            or ""
        )
        needs_project = self._project_context_needed(user_request)
        if needs_project and (
            not project or "project_context" not in context.record.request.metadata
        ):
            with timing.span(context.record.request.metadata, "project_discovery"):
                try:
                    from .project import discover_project
                    proj_ctx = discover_project()
                    if not project:
                        project = proj_ctx.name
                    context.record.request.metadata["project_context"] = proj_ctx.format_summary()
                    context.record.request.metadata["project_name"] = proj_ctx.name
                except Exception:
                    pass

        if self.memory is None and self._memory_manager is None:
            return True
        if not self._memory_retrieval_wanted(user_request):
            return True
        with timing.span(context.record.request.metadata, "memory_retrieve"):
            try:
                if self._memory_manager is not None:
                    result = self._memory_manager.retrieve(
                        query=user_request.goal,
                        project=str(project),
                        limit=5,
                    )
                elif self.memory is not None:
                    query = MemoryQuery(query=user_request.goal)
                    result = self.memory.retrieve(query)
                else:
                    result = None

                if result and result.entries:
                    context.record.request.metadata["retrieved_memories"] = [
                        entry.content for entry in result.entries
                    ]
            except Exception:
                # Memory retrieval failure must not crash execution
                pass
        return True

    def _execution_loop(
        self, context: ExecutionContext, user_request: UserRequest,
        *, on_progress: Any = None, seed_plan: ExecutionPlan | None = None,
    ) -> ExecutionResult:
        """Bounded observation-driven decision/execution loop.

        Each cycle:
          1. Reason / Plan  (planner sees full context including past observations)
          2. For each step in the plan:
             a. Check permission
             b. Execute
             c. Capture observation
             d. Evaluate observation → continue / re-plan / finish
             e. Verify when applicable
          3. A plan completes normally once its steps finish - no extra
             step or model call is needed for that common case. Re-planning
             instead happens when the context/observations actually call
             for more reasoning: a step's observation can request it
             directly (e.g. a flagged verification failure), or the planner
             can mark the plan itself (`needs_replanning`) when it already
             expects its steps to surface information it will need before
             the goal is done. Either way, the next cycle sees the
             observations already gathered.
        """
        cycles_used = 0
        previous_signature: tuple[tuple[str, str, str], ...] | None = None
        completed_signatures: set[tuple[str, str, str]] = set()

        while cycles_used < self.max_cycles:
            cycles_used += 1

            # Cancellation is cooperative and checked at each safe boundary.
            raise_if_cancelled()

            # ── Reason / Plan ──
            if cycles_used == 1 and seed_plan is not None:
                # Fast path: run the seeded single step through the SAME
                # _execute_plan pipeline (capability/target-binding/permission/
                # verify/cancel all preserved) and skip the planning model call.
                context.transition_to(ExecutionState.PLANNING)
                context.attach_plan(seed_plan)
                context.transition_to(ExecutionState.EXECUTING)
                plan = seed_plan
            else:
                if on_progress:
                    on_progress("Planning...")
                plan = self._reason(context)
                if plan is None:
                    return context.record.to_result()

            # Filter out steps that have already completed in prior cycles
            remaining_steps = [
                s for s in plan.steps if _step_signature(s) not in completed_signatures
            ]

            if not remaining_steps and plan.steps:
                # All steps planned have already executed successfully in earlier cycles!
                # The workflow is complete; finish cleanly without looping.
                break

            # ── Loop-prevention: check if the remaining plan repeats an identical unprogressed sequence ──
            remaining_signature = tuple(_step_signature(s) for s in remaining_steps)
            if remaining_signature == previous_signature:
                context.mark_failed(
                    "replanning produced an identical plan with no new "
                    "information; stopping to avoid a pointless loop"
                )
                return context.record.to_result()
            previous_signature = remaining_signature

            # Create an effective plan with remaining steps
            effective_plan = replace(plan, steps=tuple(remaining_steps))

            # ── Execute plan steps ──
            if on_progress:
                on_progress("Executing...")
            outcome = self._execute_plan(context, effective_plan, user_request, completed_signatures, on_progress=on_progress)

            if outcome == _StepOutcome.AWAITING_APPROVAL:
                reason = (
                    self._pending_approval.reason
                    if self._pending_approval
                    else "action requires user confirmation"
                )
                return self._awaiting_approval_result(context, reason)

            if outcome == _StepOutcome.FAILED:
                # Context already marked failed
                return context.record.to_result(output=_last_observation_content(context))

            if outcome == _StepOutcome.REPLAN:
                # An observation asked for re-planning, or the planner
                # flagged this plan as needing a follow-up cycle. Loop back
                # to reason/plan with the updated context.
                continue

            if outcome == _StepOutcome.FINISHED:
                break

        else:
            # Bounded execution exhausted
            context.mark_failed(
                f"execution exhausted: {cycles_used} reasoning cycles "
                f"without completing the goal"
            )
            return context.record.to_result()

        # ── Memory Update ──
        _t_memw = time.perf_counter()
        self._update_memory(context, user_request)
        timing.mark(user_request.metadata, "memory_write", time.perf_counter() - _t_memw)

        # ── Final Response ──
        context.transition_to(ExecutionState.COMPLETED)
        return context.record.to_result(output=_last_observation_content(context))

    def _reason(self, context: ExecutionContext) -> ExecutionPlan | None:
        """Invoke the planner to produce a plan from current context.

        The planner receives the full ExecutionContext including all prior
        observations, enabling observation-driven reasoning.

        Returns the plan, or None if planning failed (context marked failed).
        """
        context.transition_to(ExecutionState.PLANNING)
        try:
            _t_plan = time.perf_counter()
            plan = self.planner.plan(context)
            timing.mark(context.record.request.metadata, "planning", time.perf_counter() - _t_plan)
            if plan is None or not plan.steps:
                context.mark_failed("planner produced no execution steps")
                return None
            context.attach_plan(plan)
            context.transition_to(ExecutionState.EXECUTING)
            return plan
        except MambaCancelledError:
            raise
        except CoreError as exc:
            context.mark_failed(str(exc))
            return None
        except Exception as exc:
            context.mark_failed(f"planning failed: {exc}")
            return None

    def _execute_plan(
        self,
        context: ExecutionContext,
        plan: ExecutionPlan,
        user_request: UserRequest,
        completed_signatures: set[tuple[str, str, str]] | None = None,
        on_progress: Any = None,
    ) -> str:
        """Execute steps from a plan, evaluating each observation.

        A step's own observation can request replanning mid-plan (e.g. a
        failed verification flagged for retry). Once every step in the plan
        has executed successfully, the plan completes (FINISHED) unless the
        planner itself flagged, via `plan.metadata["needs_replanning"]`,
        that it already expects those steps to surface information it
        needs before it can continue - in which case control returns to
        the caller as REPLAN so a new cycle can reason over the results
        just observed.

        Returns the overall outcome: FINISHED, REPLAN, FAILED, or AWAITING_APPROVAL.
        """
        for idx, step in enumerate(plan.steps):
            outcome, info = self._execute_step(context, step, on_progress=on_progress)
            if outcome == _StepOutcome.FINISHED:
                if completed_signatures is not None:
                    completed_signatures.add(_step_signature(step))
                step_summary = {
                    "intent": step.intent,
                    "description": step.description,
                    "target": str(
                        step.metadata.get("path")
                        or step.metadata.get("command")
                        or step.metadata.get("url")
                        or ""
                    ),
                }
                completed_list = context.record.request.metadata.setdefault("completed_steps", [])
                completed_list.append(step_summary)
            elif outcome == _StepOutcome.AWAITING_APPROVAL:
                self._pending_approval = PendingApproval(
                    step=step,
                    context=context,
                    plan=plan,
                    step_index=idx,
                    user_request=user_request,
                    reason=info,
                    # Capture the approved identity NOW: a user "yes" applies to
                    # this action + bound target only, never to a later mutation.
                    signature=_approval_signature(step),
                )
                return _StepOutcome.AWAITING_APPROVAL
            else:
                return outcome

        if _plan_needs_replanning(plan):
            return _StepOutcome.REPLAN

        return _StepOutcome.FINISHED

    def _execute_step(
        self, context: ExecutionContext, step: PlanStep, *, on_progress: Any = None,
    ) -> tuple[str, str]:
        """Execute a single step through the full permission → execute → observe → verify pipeline.

        Returns (outcome, info) where outcome is FINISHED, REPLAN, FAILED, or AWAITING_APPROVAL.
        """
        # Never begin a step (especially a destructive or externally visible one)
        # once cancellation has been requested.
        raise_if_cancelled()

        # ── Capability Availability Check ──
        if self.capabilities is not None:
            cap = self.capabilities.find_capability_for_action(step.intent)
            if cap is None and step.metadata.get("capability_id"):
                cap = self.capabilities.get_capability(step.metadata["capability_id"])
            if cap is not None and not cap.is_available:
                if cap.status == CapabilityStatus.NOT_CONFIGURED or not cap.provider_configured:
                    reason = f"I have {cap.name.lower()} capabilities, but no {cap.name.lower()} provider is currently configured."
                elif cap.status == CapabilityStatus.DISABLED:
                    reason = f"The {cap.name.lower()} capability is currently disabled."
                else:
                    reason = f"Action '{step.intent}' is currently unavailable under {cap.name}."
                obs = Observation(
                    step_id=step.id,
                    content=reason,
                    success=False,
                    metadata={"capability_unavailable": True, "capability_id": cap.capability_id},
                )
                context.add_observation(obs)
                context.mark_failed(reason)
                return _StepOutcome.FAILED, reason

        # ── Browser target binding & expected-outcome pinning ──
        # Browser work needs the same guarantees as desktop work: the page the
        # step is bound to is pinned on the step, and the outcome the user asked
        # for is recorded up front so verification checks the page rather than
        # trusting that a click or keystroke was delivered.
        browser_error = self._bind_browser_target(step)
        if browser_error:
            obs = Observation(
                step_id=step.id,
                content=browser_error,
                success=False,
                metadata={
                    "error": "target_binding_failed",
                    "target_bound": False,
                    "action": step.intent,
                },
            )
            context.add_observation(obs)
            context.mark_failed(browser_error)
            return _StepOutcome.FAILED, browser_error

        # ── Carry the exact bound application window (launch → act) ──
        # When an earlier step in this execution bound a window, a later step
        # acting on the same application inherits that window's handle and
        # owning-process identity, so the action cannot drift onto another
        # matching window. The identity is revalidated immediately before the
        # action; a stale or replaced target is refused downstream, never
        # silently rebound.
        self._carry_cross_app_binding(step, context)

        # ── Cross-app target binding (Notepad) ──
        # The action target is bound and validated *before* permission
        # evaluation and before execution: the typed text, the intended
        # application, and the expected outcome are all pinned onto this step,
        # so a stale target or a different application can never be inferred at
        # execution time. If the target cannot be bound safely, execution stops
        # here rather than acting on an unknown application.
        binding_error = self._bind_cross_app_target(step)
        if binding_error:
            obs = Observation(
                step_id=step.id,
                content=binding_error,
                success=False,
                metadata={
                    "error": "target_binding_failed",
                    "target_bound": False,
                    "action": step.intent,
                },
            )
            context.add_observation(obs)
            context.mark_failed(binding_error)
            return _StepOutcome.FAILED, binding_error

        # ── Permission ──
        if self.permissions is not None:
            allowed, reason, requires_approval = self._evaluate_permission(step, context)
            if not allowed:
                if requires_approval:
                    return _StepOutcome.AWAITING_APPROVAL, reason
                obs = Observation(
                    step_id=step.id,
                    content=reason,
                    success=False,
                    metadata={"permission_decision": "denied"},
                )
                context.add_observation(obs)
                context.mark_failed(reason)
                return _StepOutcome.FAILED, reason

        # ── Execute ──
        # Fast-path deterministic resolution: arithmetic was computed safely in
        # quickpath (no eval) and this step already cleared the capability,
        # target-binding and permission checks above; it still flows through the
        # shared observation + verification handling below. Nothing is bypassed.
        if step.metadata.get("fast_path") and "resolved_text" in step.metadata:
            raise_if_cancelled()
            observation = Observation(
                step_id=step.id,
                content=step.metadata["resolved_text"],
                success=True,
                metadata={"fast_path": True},
            )
            context.add_observation(observation)
        else:
            try:
                _t_exec = time.perf_counter()
                observation = self.executor.execute(step, context)
                timing.mark(context.record.request.metadata, "execution", time.perf_counter() - _t_exec)
                context.add_observation(observation)
            except MambaCancelledError:
                # Cancellation must unwind, not be recorded as a step failure.
                raise
            except CoreError as exc:
                obs = Observation(step_id=step.id, content=str(exc), success=False)
                context.add_observation(obs)
                context.mark_failed(str(exc))
                return _StepOutcome.FAILED, str(exc)
            except Exception as exc:
                obs = Observation(step_id=step.id, content=str(exc), success=False)
                context.add_observation(obs)
                context.mark_failed(f"execution error: {exc}")
                return _StepOutcome.FAILED, str(exc)

        # Honour a cancellation that arrived while the step ran (e.g. an
        # interrupted MCP call) BEFORE interpreting the observation, so a
        # cancelled step is recorded as CANCELLED rather than triggering a
        # replan or being marked failed.
        raise_if_cancelled()

        # ── Evaluate Observation ──
        if not observation.success:
            # The action failed. Allow re-planning so the agent can adapt/recover, unless explicitly forbidden.
            allow_replan = step.metadata.get("replan_on_failure", True)
            if observation.metadata.get("replan") is True or allow_replan:
                return _StepOutcome.REPLAN, ""
            context.mark_failed(observation.content or "step execution failed")
            return _StepOutcome.FAILED, observation.content or "step execution failed"

        if observation.metadata.get("replan") is True:
            # Action succeeded but indicates the plan should be reconsidered
            # (e.g. discovered new information that changes the approach).
            return _StepOutcome.REPLAN, ""

        # ── Verification ──
        if self.verifier is not None and self._needs_verification(step, observation):
            if on_progress:
                on_progress("Verifying...")
            _t_ver = time.perf_counter()
            verified, reason = self._verify(step, observation)
            timing.mark(context.record.request.metadata, "verification", time.perf_counter() - _t_ver)
            if verified:
                # Surface the verified outcome explicitly, so the difference
                # between "the action executed" and "the requested outcome was
                # confirmed" is visible in the observations and in the final
                # response rather than being implied.
                if reason:
                    # The action ran and was observed, but the step stated nothing
                    # to check against: report that honestly instead of implying
                    # the outcome was confirmed.
                    context.add_observation(
                        Observation(
                            step_id=step.id,
                            content=reason,
                            success=True,
                            metadata={
                                "action": "verification_unverified",
                                "step_id": step.id,
                                "verified": False,
                                "outcome_verified": False,
                            },
                        )
                    )
                else:
                    context.add_observation(
                        Observation(
                            step_id=step.id,
                            content=self._verified_outcome_message(step),
                            success=True,
                            metadata={
                                "action": "verification_passed",
                                "step_id": step.id,
                                "verified": True,
                                "outcome_verified": True,
                            },
                        )
                    )
            else:
                # Verification failure: allow replanning unless explicitly disabled
                allow_replan = step.metadata.get("replan_on_verification_failure", True)
                if allow_replan:
                    verification_obs = Observation(
                        step_id=step.id,
                        content=f"Verification failed for step '{step.description}': {reason}",
                        success=False,
                        metadata={
                            "action": "verification",
                            "step_id": step.id,
                            "error": reason,
                            "replan": True,
                        },
                    )
                    context.add_observation(verification_obs)
                    return _StepOutcome.REPLAN, ""
                context.mark_failed(reason)
                return _StepOutcome.FAILED, reason

        return _StepOutcome.FINISHED, ""

    def _bind_browser_target(self, step: PlanStep) -> str:
        """Pin a browser step's target and expected outcome before it runs.

        Two things are frozen onto the step:

        * the **expected outcome** the user asked for (text to appear, the page
          to be on a URL, the title to contain something), so verification checks
          the page instead of the fact that an action was performed. When the
          step states no expectation, it is marked so the result is reported as
          executed-but-unverified rather than assumed successful.
        * the **bound page**, when an earlier step in the same plan already bound
          one, so a later action cannot drift onto a different tab.

        Changing tabs or opening a new page is never inferred: a step that wants
        another page must say so.

        Returns an error string when the step cannot be prepared safely.
        """
        intent = step.intent.strip().lower()
        if intent not in _BROWSER_INTENTS:
            return ""

        action = str(
            step.metadata.get("action") or step.metadata.get("browser_action") or ""
        ).strip().lower()

        # URL sanity for navigation steps: refuse to "open" nothing or a scheme we
        # do not support instead of navigating somewhere unintended.
        if intent in _BROWSER_NAVIGATE_INTENTS or action in ("open", "navigate"):
            if intent not in ("browser_back", "go_back", "navigate_back",
                              "browser_forward", "go_forward", "navigate_forward",
                              "browser_reload", "reload_page", "refresh_page"):
                url = str(
                    step.metadata.get("url")
                    or step.metadata.get("target_url")
                    or step.metadata.get("href")
                    or step.metadata.get("site")
                    or ""
                ).strip()
                if not url:
                    return (
                        "Refusing to navigate: no URL was provided for the browser step."
                    )

        expectation = _browser_expectation(step)
        if expectation:
            step.metadata["expected"] = expectation
            step.metadata["outcome_observable"] = True
            step.metadata["explicit_expectation"] = True
        elif intent in _BROWSER_NAVIGATE_INTENTS:
            # Navigation's natural outcome is "that page is loaded": verifiable
            # without the user having to restate it.
            step.metadata["outcome_observable"] = True
            step.metadata["explicit_expectation"] = False
        else:
            # An interaction with no stated outcome is executed but only
            # reported as not independently verified — never assumed successful.
            step.metadata["outcome_observable"] = False
            step.metadata.setdefault("expected", {})
        return ""

    def _verify_browser_outcome(
        self,
        step: PlanStep,
        observation: Observation,
    ) -> tuple[bool, str]:
        """Verify a browser step by comparing the observed page to the expectation.

        The page state the capability observed (URL, title, visible text) is
        compared with the step's expected predicate through the existing
        verifier. If the page cannot be observed, the outcome is reported as
        inconclusive — never as success.
        """
        expected = step.metadata.get("expected")
        if not isinstance(expected, dict) or not expected:
            explicit = bool(step.metadata.get("explicit_expectation"))
            if not explicit and step.intent.strip().lower() in _BROWSER_NAVIGATE_INTENTS:
                # Navigation is verified by having arrived on a real page.
                url = str(observation.metadata.get("page_url") or "")
                if url and not url.startswith("about:"):
                    return True, ""
                return False, (
                    "verification failed: the browser did not land on a page "
                    f"(current URL is '{url or 'unknown'}')"
                )
            return True, self._browser_unverified_reason(observation)

        actual_text = observation.metadata.get("actual")
        if not isinstance(actual_text, str) or not actual_text.strip():
            return False, (
                "verification inconclusive: the page state after the browser action "
                "could not be observed"
            )

        metadata = {**dict(step.metadata), **dict(observation.metadata)}
        predicate: dict[str, Any] = {}
        if expected.get("url_contains"):
            predicate["url_contains"] = expected["url_contains"]
        if expected.get("title_contains"):
            predicate["title_contains"] = expected["title_contains"]
        if expected.get("contains"):
            predicate["contains"] = expected["contains"]

        url = str(metadata.get("page_url") or "")
        title = str(metadata.get("page_title") or "")
        failures: list[str] = []

        def _check(expect: Any, candidate: str, label: str) -> None:
            needle = str(expect).strip().casefold()
            if needle and needle not in candidate.casefold():
                failures.append(f"{label} {candidate.strip()!r} does not contain {str(expect)!r}")

        if "url_contains" in predicate:
            _check(predicate["url_contains"], url, "the page URL")
        if "title_contains" in predicate:
            _check(predicate["title_contains"], title, "the page title")

        if "contains" in predicate:
            request = VerificationRequest(
                expected={"contains": predicate["contains"]},
                actual=actual_text,
                metadata={**metadata, "capability": "browser"},
            )
            try:
                result: VerificationResult = self.verifier.verify(request)
            except Exception as exc:
                return False, f"verification evaluation error: {exc}"
            if result.status == VerificationStatus.FAILED:
                seen = actual_text.strip()
                if len(seen) > 200:
                    seen = seen[:200] + "…"
                return False, (
                    f"verification failed: the page does not show {str(predicate['contains'])!r} "
                    f"(observed: {seen!r})"
                )
            if result.status == VerificationStatus.INCONCLUSIVE:
                return False, f"verification inconclusive: {result.reason}"

        if failures:
            return False, "verification failed: " + "; ".join(failures)
        return True, ""

    def _browser_unverified_reason(self, observation: Observation) -> str:
        """Describe what was observed when no expectation was stated to check."""
        detail = "the requested page content could not be observed"
        page_url = str(observation.metadata.get("page_url") or "")
        seen = str(observation.metadata.get("actual") or "")
        if page_url or seen:
            shown = seen.strip().replace("\n", " ")
            if len(shown) > 160:
                shown = shown[:160] + "…"
            detail = (
                "the page state was observed"
                + (f" (url: {page_url})" if page_url else "")
                + (f": {shown}" if shown else "")
            )
        return f"executed but not independently verified: {detail}"

    def _bind_cross_app_target(self, step: PlanStep) -> str:
        """Bind and freeze the application action target on a plan step.

        Guarantees, before permission evaluation and execution:

        * the requested application resolves to a *supported* adapter — a step
          naming an unsupported or unknown application is refused rather than
          acted on;
        * the application requires an explicit name unless its intent already
          names one (Notepad-flavoured intents), so nothing is inferred from
          whichever window happens to be focused;
        * the payload text is captured on the step, so the same text is what
          gets typed, observed, and verified;
        * the expected outcome predicate is pinned onto the step — verification
          checks whether the *requested text* appeared, not whether keystrokes
          were sent — but only when the application declares an observation
          mechanism. Applications with no reliable observation are marked so the
          result is reported as executed-but-unverified instead of assumed.

        Returns an error string when the target cannot be bound safely, or ""
        when the step is bound (or is not a cross-app step at all).
        """
        intent = step.intent.strip().lower()
        if intent not in _CROSS_APP_INTENTS:
            return ""

        app_argument = _step_app_argument(step)

        if intent in _CROSS_APP_INSPECT_INTENTS:
            # Pure discovery: nothing is acted on, so no target must be bound.
            return ""

        if intent in _CROSS_APP_LAUNCH_INTENTS:
            # Launching needs no pre-bound window, but a named application must
            # resolve to a supported adapter before anything is started.
            if app_argument:
                adapter = self._resolve_application(app_argument)
                if adapter is None:
                    return self._unsupported_application_message(app_argument)
                step.metadata.setdefault("app_id", adapter.app_id)
                step.metadata.setdefault("app", adapter.display_name)
            return ""

        if intent in _CROSS_APP_TEXT_INTENTS:
            text = _step_text_argument(step)
            if not text:
                return "Refusing to type: the request did not specify any text to type."

            # The application may be named directly, carried from a previously
            # bound window, or implied by an explicit window handle. A handle is
            # only ever accepted if it verifies as the requested application, so
            # naming the application explicitly remains the unambiguous case.
            if not app_argument:
                app_argument = str(step.metadata.get("app_id") or "")
            if not app_argument and step.metadata.get("hwnd") is None:
                if self._implies_notepad(step):
                    app_argument = _NOTEPAD_APP
                else:
                    return (
                        "Refusing to type: no target application was specified. State "
                        "which supported application to type into."
                    )

            if app_argument:
                adapter = self._resolve_application(app_argument, required=True)
                if adapter is None:
                    return self._unsupported_application_message(app_argument)
                if not getattr(adapter, "supports_text_input", False):
                    return (
                        f"Refusing to type: typing text into {adapter.display_name} is not "
                        "a supported action."
                    )
                step.metadata["app_id"] = adapter.app_id
                step.metadata["app"] = adapter.display_name

                observable = bool(getattr(adapter, "probes", ()))
                step.metadata["outcome_observable"] = observable
                expected = step.metadata.get("expected")
                if observable and (
                    not isinstance(expected, dict) or _UI_TEXT_PREDICATE_KEY not in expected
                ):
                    step.metadata["expected"] = {
                        _UI_TEXT_PREDICATE_KEY: {
                            "app": adapter.app_id,
                            "app_name": adapter.display_name,
                            "text": text,
                        }
                    }
                elif not observable:
                    # No observation mechanism: never pin an expected predicate that
                    # could only be "confirmed" by the action's own report.
                    if isinstance(expected, dict) and _UI_TEXT_PREDICATE_KEY in expected:
                        step.metadata.pop("expected", None)

            # Pre-bind the exact window this step will act on, through the same
            # binding path the executing skill uses. The pinned identity (handle,
            # owning process, class) is what a user approval refers to, and it is
            # re-verified live immediately before the keystrokes are sent — so a
            # stale, foreign, or ambiguous target stops the step here, before
            # permission, instead of being guessed at execution time.
            prebind_error = self._prebind_cross_app_window(step)
            if prebind_error:
                return prebind_error

            # Pin the payload used for typing and for verification.
            step.metadata["text"] = text
            return ""

        return ""

    def _prebind_cross_app_window(self, step: PlanStep) -> str:
        """Pin the exact application window a text step will act on (read-only).

        Delegates to the desktop capability's own pre-bind (the same adapter
        resolution and binding path the executing skill uses), so the identity
        pinned here is exactly what execution re-verifies immediately before
        typing. Returns a refusal message when the target is stale, foreign,
        ambiguous, or absent; "" when pinned — or when no pre-bind seam is
        wired, in which case execution reports its own binding result.
        """
        handlers = getattr(self.executor, "handlers", None)
        if not isinstance(handlers, Mapping):
            return ""
        candidates = (step.intent.strip().lower(), *sorted(_CROSS_APP_TEXT_INTENTS))
        handler = None
        for intent in candidates:
            candidate = handlers.get(intent)
            if candidate is not None and callable(
                getattr(candidate, "prebind_text_target", None)
            ):
                handler = candidate
                break
        if handler is None:
            return ""
        try:
            pinned, error = handler.prebind_text_target(dict(step.metadata))
        except Exception as exc:  # pragma: no cover - defensive: never break the step
            return f"the application target could not be bound safely: {exc}"
        if error:
            return error
        for key in (
            "hwnd",
            "target_pid",
            "target_title",
            "target_class",
            "target_process",
            "app_id",
            "app",
        ):
            if pinned.get(key) is not None:
                step.metadata[key] = pinned[key]
        return ""

    def _carry_cross_app_binding(self, step: PlanStep, context: ExecutionContext) -> None:
        """Pin a previously bound application window onto an acting step.

        When one step of this execution bound a window (a launch, or an
        explicit target), a later step acting on the same application
        (type/read/close) inherits the exact bound window: its handle, owning
        process identity, title, class, process, and application. Downstream
        the window is revalidated against its live state immediately before
        the action, so a stale or replaced target is refused instead of being
        silently rebound to another matching window.

        A step that already names a window handle is left untouched, and
        nothing is ever inferred from the foreground window. When the step
        names an application, only a binding recorded for that application is
        carried. This is a no-op when nothing was bound in this execution.
        """
        # Ownership is never taken from the plan: any value the plan claims is
        # discarded here and recomputed below from trusted execution evidence.
        step.metadata.pop("target_owned", None)

        intent = step.intent.strip().lower()
        if intent not in _CROSS_APP_CARRY_INTENTS:
            return
        if step.metadata.get("hwnd") is not None:
            # A window is already pinned on this step (carried earlier, pinned
            # by pre-binding, or named by the plan). Recompute ownership for it
            # so the value always reflects launch evidence from this execution.
            self._set_target_ownership(step, context)
            return

        pinned: set[str] = set()
        for key in ("app_id", "app"):
            value = step.metadata.get(key)
            if isinstance(value, str) and value.strip():
                pinned.add(value.strip().lower())

        for observation in reversed(context.observations):
            if not observation.success:
                continue
            meta = observation.metadata
            if meta.get("hwnd") is None or meta.get("target_pid") is None:
                continue
            carried_names = {
                str(meta.get(key) or "").strip().lower()
                for key in ("app_id", "app")
            }
            if pinned and not (pinned & carried_names):
                continue
            try:
                hwnd = int(meta["hwnd"])
                target_pid = int(meta["target_pid"])
            except (TypeError, ValueError):
                continue
            step.metadata.setdefault("hwnd", hwnd)
            step.metadata.setdefault("target_pid", target_pid)
            for key in ("target_title", "target_class", "target_process"):
                value = meta.get(key)
                if value is not None:
                    step.metadata.setdefault(key, value)
            app_id = str(meta.get("app_id") or "").strip()
            if app_id:
                step.metadata.setdefault("app_id", app_id)
            app_name = str(meta.get("app") or "").strip()
            if app_name:
                step.metadata.setdefault("app", app_name)
            self._set_target_ownership(step, context)
            return

    def _set_target_ownership(self, step: PlanStep, context: ExecutionContext) -> None:
        """Record whether this execution's own evidence shows Mamba created the window.

        Ownership is positive evidence only: a successful launch observation
        from this execution (``launched`` is set by the desktop driver's own
        launch, never by the plan) whose window handle and owning process match
        the identity pinned on this step. Without that evidence the window is
        treated as the user's own — a consequential action on it requires the
        user's confirmation instead of proceeding silently.
        """
        try:
            hwnd = int(step.metadata.get("hwnd"))
            target_pid = int(step.metadata.get("target_pid"))
        except (TypeError, ValueError):
            return
        for observation in context.observations:
            if not observation.success:
                continue
            meta = observation.metadata
            if not meta.get("launched"):
                continue
            try:
                if int(meta.get("hwnd")) != hwnd or int(meta.get("target_pid")) != target_pid:
                    continue
            except (TypeError, ValueError):
                continue
            step.metadata["target_owned"] = True
            return

    # ── application resolution (delegates to the desktop capability) ──

    def _application_registry(self) -> Any:
        """The desktop capability's application registry, when it is wired.

        Application-scoped handlers (the Notepad-scoped wiring) report the
        narrower registry they actually act on, so this reflects what the
        capability can really target rather than every adapter Mamba knows about.
        """
        executor = self.executor
        handlers = getattr(executor, "handlers", None)
        if not isinstance(handlers, Mapping):
            return None
        candidates = (
            "launch_notepad",
            "type_text_in_notepad",
            "read_notepad_text",
            "launch_application",
            "type_text",
            "read_application_text",
            "inspect_applications",
        )
        for intent in candidates:
            handler = handlers.get(intent)
            if handler is None:
                continue
            registry = getattr(handler, "_registry", None)
            if registry is None:
                registry = getattr(handler, "registry", None)
            if registry is None:
                continue
            try:
                if not list(registry.all()):
                    continue
            except Exception:
                pass
            return registry
        return None

    def _resolve_application(self, name: str, *, required: bool = False) -> Any:
        """Resolve an application name to a supported adapter, if one exists."""
        if not name:
            return None
        registry = self._application_registry()
        if registry is None:
            return None
        try:
            return registry.resolve(name)
        except Exception:
            return None

    def _unsupported_application_message(self, name: str) -> str:
        registry = self._application_registry()
        supported = ""
        if registry is not None:
            try:
                supported = ", ".join(a.app_id for a in registry.all())
            except Exception:
                supported = ""
        suffix = f" Mamba currently supports: {supported}." if supported else ""
        return (
            f"'{name}' is not a supported application, so Mamba cannot interact with "
            f"it.{suffix}"
        )

    def _implies_notepad(self, step: PlanStep) -> bool:
        """Whether an unnamed target should be resolved as Notepad.

        True when the intent itself names Notepad, or when the wired desktop
        capability offers Notepad as its only supported application (the
        Notepad-scoped wiring). A runtime that can act on several applications
        gets no implicit default: the application must be named.
        """
        if step.intent.strip().lower() in _NOTEPAD_FLAVOURED_INTENTS:
            return True
        registry = self._application_registry()
        if registry is None:
            return False
        try:
            adapters = registry.all()
        except Exception:
            return False
        return len(adapters) == 1 and adapters[0].app_id == "notepad"

    def _execute_application_read(
        self,
        metadata: dict[str, Any],
    ) -> tuple[str | None, str]:
        """Observe a bound application window's content through the desktop capability.

        Reuses the single existing read path (the same tools the
        ``read_application_text`` / ``read_notepad_text`` skills use) rather than
        adding a second observation mechanism.

        Returns (text, error). ``text`` is None when nothing could be observed.
        """
        handlers = getattr(self.executor, "handlers", None)
        if not isinstance(handlers, Mapping):
            return None, "no desktop capability is wired to observe application content"

        read_intent = next(
            (intent for intent in _CROSS_APP_READ_INTENTS if intent in handlers), None
        )
        if read_intent is None:
            return None, "no desktop capability is wired to observe application content"

        target_meta = {
            key: metadata[key]
            for key in (
                "app",
                "app_id",
                "hwnd",
                "target_title",
                "target_pid",
                "target_class",
                "target_process",
            )
            if metadata.get(key) is not None
        }
        read_input = TaskInput(
            step_id="app-content-read",
            description="Observe bound application content for outcome verification",
            intent=read_intent,
            execution_id="verification",
            goal="verify application outcome",
            step_metadata=target_meta,
        )
        try:
            output = handlers[read_intent].run(read_input, None)  # type: ignore[arg-type]
        except Exception as exc:
            return None, f"observing application content for verification failed: {exc}"

        if not output.success:
            return None, output.content or "the application's content could not be observed"
        text = output.metadata.get("text")
        if not isinstance(text, str):
            return None, "the application's content could not be observed"
        return text, ""

    def _evaluate_permission(
        self, step: PlanStep, context: ExecutionContext,
    ) -> tuple[bool, str, bool]:
        """Evaluate permissions for a plan step.

        Returns (allowed, reason, requires_approval).
        """
        if self.permissions is None:
            return True, "", False

        cap_meta: dict[str, Any] = {}
        if hasattr(self.executor, "get_metadata"):
            try:
                cap_meta = dict(self.executor.get_metadata(step) or {})
            except Exception:
                cap_meta = {}

        action = cap_meta.get("action") or step.metadata.get("action") or step.intent or "execute"
        tool_name = (
            cap_meta.get("tool_name")
            or step.metadata.get("tool_name")
            or step.metadata.get("capability")
            or "step"
        )

        # Risk is classified by authoritative capability/tool metadata. A step may
        # only ever raise that classification: nothing a plan claims can lower a
        # capability's own risk level.
        risk_level = _max_risk_level(
            _coerce_risk_level(cap_meta.get("risk_level")),
            _coerce_risk_level(step.metadata.get("risk_level")),
        )

        # Sensitivity flags are authored by the capability table alone. Carrying a
        # plan's own copy forward would let it clear a flag the capability set, so
        # the merged metadata is rebuilt from authoritative input.
        merged_metadata = {
            key: value
            for key, value in step.metadata.items()
            if key not in _SENSITIVE_METADATA_KEYS
        }
        for sensitive_key in _SENSITIVE_METADATA_KEYS:
            if cap_meta.get(sensitive_key) is True:
                merged_metadata[sensitive_key] = True

        perm_req = PermissionRequest(
            action=action,
            tool_name=tool_name,
            risk_level=risk_level,
            resource=step.metadata.get("resource"),
            reason=step.description,
            metadata=merged_metadata,
        )

        try:
            perm_res = self.permissions.evaluate(perm_req)
        except Exception as exc:
            return False, f"permission evaluation failed: {exc}", False

        if perm_res.decision == PermissionDecision.DENY:
            return False, f"permission denied: {perm_res.reason}", False

        if perm_res.decision == PermissionDecision.ASK:
            # Approval is a fact about the user, recorded by Mamba when a real
            # confirmation arrives. It is read from that record and nowhere else:
            # neither step metadata nor request metadata can stand in for it, so a
            # plan cannot authorize its own action.
            if step.id not in self._approved_step_ids:
                return False, perm_res.reason or f"Action '{step.description}' requires user confirmation", True
            if _approval_signature(step) not in self._approved_signatures:
                # The step this execution is about is not the action+target the
                # user approved (it changed since, e.g. after replanning).
                # Approval never transfers to a materially changed step.
                return False, perm_res.reason or f"Action '{step.description}' requires user confirmation", True

        return True, "", False

    @staticmethod
    def _approval_target_metadata(step: PlanStep) -> dict[str, Any]:
        """The bound target an approval refers to, when the step pins one."""
        metadata: dict[str, Any] = {}
        for key in ("hwnd", "target_pid", "app", "app_id", "path", "command"):
            value = step.metadata.get(key)
            if value is not None:
                metadata[key] = value
        return metadata

    def _awaiting_approval_result(
        self, context: ExecutionContext, reason: str,
    ) -> ExecutionResult:
        """Record and return the standard pause-for-confirmation result.

        The observation carries the reason, the paused step, and the exact
        bound target the approval refers to, so the confirmation the user
        gives is tied to the identity that will actually execute.
        """
        pending = self._pending_approval
        prompt_msg = f"Action requires user confirmation: {reason}. Do you want to proceed?"
        obs = Observation(
            step_id=pending.step.id if pending else "approval",
            content=prompt_msg,
            success=False,
            metadata={
                "permission_decision": "ask",
                "awaiting_approval": True,
                "reason": reason,
                **(self._approval_target_metadata(pending.step) if pending else {}),
            },
        )
        context.add_observation(obs)
        context.mark_failed(prompt_msg)
        return context.record.to_result(output=prompt_msg)

    def _expired_approval_result(self, pending: PendingApproval) -> ExecutionResult:
        """Report that a stale approval elapsed and was discarded.

        Nothing executes: the paused action is revoked and the user is asked
        to re-initiate it if they still want it.
        """
        context = ExecutionContext.from_request(
            UserRequest(
                goal=pending.user_request.goal,
                metadata=dict(pending.user_request.metadata),
            )
        )
        context.transition_to(ExecutionState.PLANNING)
        msg = (
            f"Approval for '{pending.step.description}' expired and was "
            "discarded. Please ask me to do it again."
        )
        plan = ExecutionPlan(steps=(PlanStep(description="Report expired approval", intent="respond"),))
        context.attach_plan(plan)
        context.transition_to(ExecutionState.EXECUTING)
        obs = Observation(
            step_id=plan.steps[0].id,
            content=msg,
            success=True,
            metadata={"approval_expired": True, "step": pending.step.description},
        )
        context.add_observation(obs)
        context.transition_to(ExecutionState.COMPLETED)
        return context.record.to_result(output=msg)

    def _resume_pending_approval(
        self,
        pending: PendingApproval,
        approval_request: UserRequest,
        *,
        on_progress: Any = None,
    ) -> ExecutionResult:
        """Resume execution of a paused step after user approval."""
        if time.monotonic() - pending.created_at >= _APPROVAL_TTL_SECONDS:
            # The confirmation window elapsed before the user responded. A
            # stale approval is discarded and must never execute.
            self.revoke_pending_approval()
            return self._expired_approval_result(pending)

        # Record the approval against this exact action + bound target.
        # Approval does not carry over to the plan's other steps: each still
        # needs its own confirmation.
        self._approved_step_ids.add(pending.step.id)
        self._approved_signatures.add(pending.signature)
        context = ExecutionContext.from_request(
            UserRequest(
                goal=pending.user_request.goal,
                metadata=dict(pending.user_request.metadata),
            )
        )
        context.transition_to(ExecutionState.PLANNING)
        context.attach_plan(pending.plan)
        context.transition_to(ExecutionState.EXECUTING)

        for obs in pending.context.observations:
            if obs.metadata.get("awaiting_approval"):
                continue
            context.add_observation(obs)

        if on_progress:
            on_progress("Executing...")

        try:
            outcome, info = self._execute_step(context, pending.step, on_progress=on_progress)
            if outcome == _StepOutcome.AWAITING_APPROVAL:
                # The step materially changed since the user was asked (its
                # action or bound target no longer matches what was approved).
                # The earlier approval is void: re-ask for the exact current
                # identity instead of executing a different action.
                self._clear_approvals()
                self._pending_approval = PendingApproval(
                    step=pending.step,
                    context=context,
                    plan=pending.plan,
                    step_index=pending.step_index,
                    user_request=pending.user_request,
                    reason=info,
                    signature=_approval_signature(pending.step),
                )
                return self._awaiting_approval_result(context, info)
            if outcome == _StepOutcome.FAILED:
                return context.record.to_result()

            remaining_steps = pending.plan.steps[pending.step_index + 1:]
            for rem_step in remaining_steps:
                outcome, rem_info = self._execute_step(context, rem_step, on_progress=on_progress)
                if outcome == _StepOutcome.AWAITING_APPROVAL:
                    self._pending_approval = PendingApproval(
                        step=rem_step,
                        context=context,
                        plan=pending.plan,
                        step_index=pending.plan.steps.index(rem_step),
                        user_request=pending.user_request,
                        reason=rem_info,
                        signature=_approval_signature(rem_step),
                    )
                    return self._awaiting_approval_result(context, rem_info)
                if outcome == _StepOutcome.FAILED:
                    return context.record.to_result()

            if outcome == _StepOutcome.REPLAN or _plan_needs_replanning(pending.plan):
                return self._execution_loop(context, pending.user_request, on_progress=on_progress)

            self._update_memory(context, pending.user_request)
            context.transition_to(ExecutionState.COMPLETED)
            if on_progress:
                on_progress("Completed")
            return context.record.to_result(output=_last_observation_content(context))
        except MambaCancelledError:
            return self._cancel_result(context, on_progress)

    def _needs_verification(
        self, step: PlanStep, observation: Observation,
    ) -> bool:
        """Determine whether verification is applicable for a step."""
        intent_lower = step.intent.lower()
        if intent_lower in (
            "send_email",
            "reply_email",
            "create_event",
            "modify_event",
            "cancel_event",
            "send_message",
            "reply_message",
        ):
            return True
        if intent_lower in _CROSS_APP_TEXT_INTENTS:
            # Typing keystrokes is an action; the *outcome* must be observed.
            return True
        if intent_lower in _BROWSER_INTENTS:
            # Browser navigation and interaction are actions; what matters is the
            # page state that results, which verification checks.
            return True
        return (
            "expected" in step.metadata
            or step.metadata.get("verify") is True
            or "expected" in observation.metadata
        )

    def _verify(
        self, step: PlanStep, observation: Observation,
    ) -> tuple[bool, str]:
        """Verify the execution outcome against expectations."""
        if self.verifier is None:
            return True, ""

        expected = step.metadata.get(
            "expected",
            observation.metadata.get("expected", UNAVAILABLE),
        )

        # ── Browser outcome verification ──
        # A browser action is not the outcome: the page it produced is. Observe
        # the page state and check the step's expectation against it.
        if step.intent.strip().lower() in _BROWSER_INTENTS:
            return self._verify_browser_outcome(step, observation)

        # ── Cross-app outcome verification ──
        # "Keystrokes were sent" is not evidence that the requested text landed
        # in the intended window. When a step's expected outcome is a UI text
        # predicate, the text is observed from the bound window after the fact
        # and the *existing* verifier decides VERIFIED / FAILED / INCONCLUSIVE.
        ui_predicate = expected.get(_UI_TEXT_PREDICATE_KEY) if isinstance(expected, dict) else None
        if ui_predicate is not None:
            return self._verify_app_outcome(step, observation, ui_predicate)

        intent_lower = step.intent.lower()
        # Measure actual physical outcome when verify is requested without explicit expected
        if expected is UNAVAILABLE:
            if step.metadata.get("verify") is True:
                target_path = step.metadata.get("path") or observation.metadata.get("path")
                if intent_lower in ("write_file", "create_file", "create_directory", "create_dir", "mkdir") and target_path:
                    if "content" in step.metadata and intent_lower in ("write_file", "create_file"):
                        expected = {"content_matches": {"path": str(target_path), "content": str(step.metadata["content"])}}
                    else:
                        expected = {"file_exists": str(target_path)}
                elif intent_lower in ("delete", "delete_file", "delete_directory", "remove", "remove_file", "rmdir", "unlink") and target_path:
                    expected = {"file_absent": str(target_path)}
            if intent_lower in (
                "send_email",
                "reply_email",
                "create_event",
                "modify_event",
                "cancel_event",
                "send_message",
                "reply_message",
            ):
                expected = {"provider_verified": True}

        actual = observation.metadata.get("result", observation.metadata.get("actual", observation.content))

        v_meta = {**dict(step.metadata), **dict(observation.metadata)}
        v_req = VerificationRequest(
            expected=expected,
            actual=actual,
            metadata=v_meta,
        )

        try:
            v_res = self.verifier.verify(v_req)
        except Exception as exc:
            return False, f"verification evaluation error: {exc}"

        if v_res.status == VerificationStatus.VERIFIED:
            return True, ""
        if v_res.status == VerificationStatus.FAILED:
            return False, f"verification failed: {v_res.reason}"
        if v_res.status == VerificationStatus.INCONCLUSIVE:
            return False, f"verification inconclusive: {v_res.reason}"

        return False, f"unknown verification status: {v_res.status}"

    def _verify_app_outcome(
        self,
        step: PlanStep,
        observation: Observation,
        predicate: Any,
    ) -> tuple[bool, str]:
        """Verify a cross-application outcome by observing the target window.

        Separates two distinct facts:

        * the action executed — the step's observation reports whether text was
          typed and whether the bound target was the active window; and
        * the requested outcome — the content actually present in the bound
          application window, observed back through the desktop capability's own
          adapter-declared mechanism.

        When the outcome cannot be observed (window gone, no observation
        mechanism, capability unavailable), the result is reported as
        INCONCLUSIVE — or, for applications with no observation mechanism at all,
        as "executed but not independently verified" — never claimed as success.
        """
        if isinstance(predicate, dict):
            expected_text = str(predicate.get("text") or "")
            predicate_app = str(predicate.get("app") or "")
            app_name = str(predicate.get("app_name") or predicate_app or "the target")
        else:
            expected_text = str(predicate or "")
            predicate_app = ""
            app_name = "the target"

        if predicate_app:
            adapter = self._resolve_application(predicate_app, required=True)
            if adapter is None:
                return False, (
                    f"verification failed: '{predicate_app}' is not a supported "
                    "application, so the outcome cannot be confirmed"
                )
            app_name = getattr(adapter, "display_name", app_name)

        if not expected_text:
            return False, "verification failed: no expected text was recorded for this step"

        if step.metadata.get("outcome_observable") is False:
            return False, (
                f"executed but not independently verified: {app_name} exposes no reliable "
                "observation mechanism, so Mamba cannot confirm the requested text arrived"
            )

        metadata = {**dict(step.metadata), **dict(observation.metadata)}
        actual_text, read_error = self._execute_application_read(metadata)

        if actual_text is None:
            return False, (
                "verification inconclusive: the requested text could not be observed in "
                f"the intended {app_name} window ({read_error})"
            )

        actual = {"ui_text": actual_text, "app": app_name}
        # Reuse the existing verifier predicate machinery: the observed window
        # text is compared with the framework's `contains` predicate rather than
        # a bespoke comparison.
        request = VerificationRequest(
            expected={"contains": expected_text},
            actual=actual,
            metadata={**metadata, "app": app_name},
        )
        try:
            result: VerificationResult = self.verifier.verify(request)
        except Exception as exc:
            return False, f"verification evaluation error: {exc}"

        if result.status == VerificationStatus.VERIFIED:
            return True, ""
        if result.status == VerificationStatus.FAILED:
            seen = actual_text.strip()
            if len(seen) > 200:
                seen = seen[:200] + "…"
            return False, (
                f"verification failed: the text observed in the bound {app_name} window "
                f"was {seen!r}, which does not contain {expected_text!r}"
            )
        return False, f"verification inconclusive: {result.reason}"

    def _verified_outcome_message(self, step: PlanStep) -> str:
        """Describe a verified outcome distinctly from a merely executed action."""
        expected = step.metadata.get("expected")

        # Browser outcomes state what was actually confirmed on the page.
        if step.intent.strip().lower() in _BROWSER_INTENTS and isinstance(expected, dict):
            details: list[str] = []
            if expected.get("url_contains"):
                details.append(f"the page is on a URL containing '{expected['url_contains']}'")
            if expected.get("title_contains"):
                details.append(f"the page title contains '{expected['title_contains']}'")
            if expected.get("contains"):
                details.append(f"the page shows '{expected['contains']}'")
            if details:
                return "Verified: " + "; ".join(details) + "."

        if isinstance(expected, dict):
            ui_predicate = expected.get(_UI_TEXT_PREDICATE_KEY)
            if isinstance(ui_predicate, dict):
                text = str(ui_predicate.get("text") or "")
                app = str(
                    ui_predicate.get("app_name")
                    or step.metadata.get("app")
                    or ui_predicate.get("app")
                    or "the target"
                )
                return (
                    f"Verified: '{text}' is present in the {app} window "
                    f"(observed after typing)."
                )
        return f"Verified: {step.description}"

    # Intents whose output is transient — not persisted as durable memory.
    _TRANSIENT_INTENTS: frozenset[str] = frozenset({
        "list_directory", "list_dir", "read_file", "system_info",
        "sys_info", "gpu_info", "screenshot", "take_screenshot",
        "capture_screen", "region_screenshot", "ocr", "read_screen",
        "region_ocr", "visual_understanding", "get_foreground_window",
        "foreground_window", "active_window", "get_window_title",
        "window_title", "find_window", "read_clipboard", "get_clipboard",
        "web_search", "list_emails", "list_events", "list_conversations",
        "search_emails", "search_events", "search_conversations",
        "read_email", "read_messages", "get_event",
        "project_info", "explain_architecture", "find_problems",
        "relevant_files", "git_context", "project_git_status",
    })

    # Mutating intents whose outcomes ARE worth persisting.
    _MUTATING_INTENTS: frozenset[str] = frozenset({
        "write_file", "create_file", "create_directory", "create_dir",
        "mkdir", "delete", "delete_file", "delete_directory", "remove",
        "execute_command", "run_command",
        "remember", "store_memory", "save_memory",
        "send_email", "reply_email", "send_message", "reply_message",
        "create_event", "modify_event", "cancel_event",
        # Cross-app actions are real, externally visible interactions.
        "launch_application", "open_application", "start_application",
        "activate_application",
        "launch_notepad", "open_notepad", "start_notepad",
        "type_text", "type_text_in_notepad", "type_in_notepad",
        "type_into_notepad", "write_in_notepad", "enter_text",
        "type_text_in_application", "write_in_application",
        # Browser navigation and interaction change a real page.
        "open_url_in_browser", "open_page", "browse_url", "visit_page",
        "navigate_browser", "browser_navigate", "go_to_url",
        "click_element", "browser_click", "click_on_page", "click_link",
        "type_text_in_page", "browser_type", "fill_field",
        "enter_text_in_page", "clear_field", "browser_clear", "clear_input",
        "press_key_in_page", "browser_press_key", "page_press_key",
        "browser_scroll", "scroll_page", "select_option", "browser_select",
        "choose_option",
    })

    def _update_memory(
        self, context: ExecutionContext, user_request: UserRequest,
    ) -> None:
        """Store genuinely durable execution outcomes in memory.

        Transient tool output (directory listings, file reads, screenshots,
        system queries, search results) is NOT persisted.  Only durable
        information is stored: explicit user memories, preferences,
        project decisions, and mutating action outcomes.

        Failures are silently absorbed.
        """
        if self.memory is None and self._memory_manager is None:
            return

        # Do not persist screen interpretations or items explicitly excluded.
        last = context.observations[-1] if context.observations else None
        if last is not None:
            action = str(last.metadata.get("action") or "")
            if action == "visual_understanding" or last.metadata.get("persist_memory") is False:
                return

        # Check if the execution plan was entirely transient read/inspection.
        plan = context.record.plan
        if plan is not None and plan.steps:
            # If all executed steps are purely transient reads (e.g. list_dir, read_file, sys_info), skip memory.
            all_transient = all(
                s.intent.strip().lower() in self._TRANSIENT_INTENTS
                for s in plan.steps
            )
            if all_transient and not (
                (last is not None and last.metadata.get("durable_memory") is True)
                or (last is not None and last.metadata.get("persist_memory") is True)
                or user_request.metadata.get("durable_memory") is True
            ):
                return

        try:
            last_content = _last_observation_content(context)
            project = (
                user_request.metadata.get("project")
                or context.record.request.metadata.get("project_name")
                or self._active_entities.get("repository")
                or ""
            )
            content = f"Goal: {user_request.goal} -> Outcome: {last_content}"

            if self._memory_manager is not None:
                from memory.types import MemoryType

                self._memory_manager.remember(
                    content=content,
                    memory_type=MemoryType.TASK_CONTEXT,
                    project=str(project),
                    source="execution",
                    importance=0.4,
                    metadata={
                        "execution_id": context.execution_id,
                        "goal": user_request.goal,
                    },
                    check_supersede=False,
                )
            elif self.memory is not None:
                self.memory.store(
                    MemoryEntry(
                        content=content,
                        metadata={
                            "execution_id": context.execution_id,
                            "goal": user_request.goal,
                        },
                    )
                )
        except Exception:
            # Memory update failure must not corrupt a successful execution
            pass


class _StepOutcome:
    """Sentinel values for step/plan execution outcomes."""

    FINISHED = "finished"
    REPLAN = "replan"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"


def create_brain(
    planner: Planner,
    executor: Executor,
    *,
    memory: MemoryStore | None = None,
    permissions: PermissionPolicy | None = None,
    verifier: Verifier | None = None,
    model_router: ModelRouter | None = None,
    max_cycles: int = _DEFAULT_MAX_CYCLES,
    capabilities: CapabilityRegistry | None = None,
    memory_manager: Any = None,
) -> Brain:
    """Convenience factory to create a Brain instance."""
    return Brain(
        planner=planner,
        executor=executor,
        memory=memory,
        permissions=permissions,
        verifier=verifier,
        model_router=model_router,
        max_cycles=max_cycles,
        capabilities=capabilities,
        memory_manager=memory_manager,
    )
