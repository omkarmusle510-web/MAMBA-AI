"""Mamba Brain - Intelligent observation-driven execution coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
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
from .context import ExecutionContext
from .errors import CoreError
from .protocols import Executor, Planner
from .state import ExecutionState
from .types import (
    ExecutionPlan,
    ExecutionResult,
    Observation,
    PlanStep,
    ResultStatus,
    UserRequest,
)

_DEFAULT_MAX_CYCLES = 10

# ── Cross-application (Notepad) target binding & outcome verification ───────
# Intents that are only ever meaningful against Windows Notepad.
_NOTEPAD_LAUNCH_INTENTS: frozenset[str] = frozenset(
    {"launch_notepad", "open_notepad", "start_notepad"}
)
_NOTEPAD_TEXT_INTENTS: frozenset[str] = frozenset(
    {
        "type_text",
        "type_text_in_notepad",
        "type_in_notepad",
        "type_into_notepad",
        "write_in_notepad",
        "enter_text",
    }
)
_NOTEPAD_READ_INTENTS: frozenset[str] = frozenset(
    {"read_notepad_text", "notepad_text", "get_notepad_text"}
)
_NOTEPAD_INTENTS: frozenset[str] = (
    _NOTEPAD_LAUNCH_INTENTS | _NOTEPAD_TEXT_INTENTS | _NOTEPAD_READ_INTENTS
)

_NOTEPAD_APP = "Notepad"
"""The only application Mamba's cross-app typing capability targets in this phase."""

_UI_TEXT_PREDICATE_KEY = "ui_text_contains"
"""Expected-predicate that verifies an outcome by reading it back out of a GUI."""

_UI_TEXT_READ_INTENT = "read_notepad_text"
"""Intent used to read a bound Notepad window's text back through the desktop
capability (the same single code path used by the tool layer)."""


def _step_text_argument(step: PlanStep) -> str:
    """Extract the text a step intends to type, from planner metadata."""
    for key in ("text", "content", "value", "input"):
        value = step.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _step_app_argument(step: PlanStep) -> str:
    for key in ("app", "application", "target_app", "program"):
        value = step.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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
        if observation.metadata.get("action") == "verification_passed":
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


@dataclass(slots=True)
class PendingApproval:
    """Represents a paused step waiting for explicit user confirmation."""

    step: PlanStep
    context: ExecutionContext
    plan: ExecutionPlan
    step_index: int
    user_request: UserRequest
    reason: str


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
    ) -> ExecutionResult:
        """Run a user request through the observation-driven execution lifecycle.

        Args:
            request: User goal as string or UserRequest.
            on_progress: Optional callable(str) receiving lightweight
                milestone updates (e.g. "Planning...", "Executing...").
        """
        _progress = on_progress if callable(on_progress) else None

        # ── 1. Request Intake ──
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
                res = self._resume_pending_approval(pending, user_request)
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
                res = self._resume_pending_approval(pending, user_request)
                self._record_turn_context(user_request, res)
                return res

        if _is_denial_phrase(user_request.goal):
            if self._pending_approval is not None:
                pending = self._pending_approval
                self._pending_approval = None
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

        # ── 1e. Conversational Referent Resolution ──
        resolved_goal = self._resolve_referents(user_request.goal)
        if resolved_goal != user_request.goal:
            user_request = replace(user_request, goal=resolved_goal)
        user_request.metadata["active_entities"] = dict(self._active_entities)

        # ── 2. Context Assembly ──
        context = ExecutionContext.from_request(user_request)

        # ── 3. Memory Retrieval ──
        if _progress:
            _progress("Understanding...")
        if not self._retrieve_memory(context, user_request):
            return context.record.to_result()

        # ── 4. Observation-Driven Execution Loop ──
        res = self._execution_loop(context, user_request, on_progress=_progress)
        self._record_turn_context(user_request, res)
        return res

    def route_model(self, request: ModelRequest) -> ModelProvider | None:
        """Route a model request through the model router if configured."""
        if self.model_router is not None:
            return self.model_router.route(request)
        return None

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
        if not project or "project_context" not in context.record.request.metadata:
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
        *, on_progress: Any = None,
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

            # ── Reason / Plan ──
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
            outcome = self._execute_plan(context, effective_plan, user_request, completed_signatures)

            if outcome == _StepOutcome.AWAITING_APPROVAL:
                reason = (
                    self._pending_approval.reason
                    if self._pending_approval
                    else "action requires user confirmation"
                )
                prompt_msg = (
                    f"Action requires user confirmation: {reason}. Do you want to proceed?"
                )
                obs = Observation(
                    step_id=(
                        self._pending_approval.step.id
                        if self._pending_approval
                        else "approval"
                    ),
                    content=prompt_msg,
                    success=False,
                    metadata={"permission_decision": "ask", "awaiting_approval": True},
                )
                context.add_observation(obs)
                context.mark_failed(prompt_msg)
                return context.record.to_result(output=prompt_msg)

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
        self._update_memory(context, user_request)

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
            plan = self.planner.plan(context)
            if plan is None or not plan.steps:
                context.mark_failed("planner produced no execution steps")
                return None
            context.attach_plan(plan)
            context.transition_to(ExecutionState.EXECUTING)
            return plan
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
            outcome, info = self._execute_step(context, step)
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
                )
                return _StepOutcome.AWAITING_APPROVAL
            else:
                return outcome

        if _plan_needs_replanning(plan):
            return _StepOutcome.REPLAN

        return _StepOutcome.FINISHED

    def _execute_step(
        self, context: ExecutionContext, step: PlanStep,
    ) -> tuple[str, str]:
        """Execute a single step through the full permission → execute → observe → verify pipeline.

        Returns (outcome, info) where outcome is FINISHED, REPLAN, FAILED, or AWAITING_APPROVAL.
        """
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

        # ── Cross-app target binding (Notepad) ──
        # The action target is bound and validated *before* permission
        # evaluation and before execution: the typed text, the intended
        # application, and the expected outcome are all pinned onto this step,
        # so a stale target or a different application can never be inferred at
        # execution time. If the target cannot be bound safely, execution stops
        # here rather than acting on an unknown application.
        binding_error = self._bind_notepad_target(step)
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
        try:
            observation = self.executor.execute(step, context)
            context.add_observation(observation)
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
            verified, reason = self._verify(step, observation)
            if verified:
                # Surface the verified outcome explicitly, so the difference
                # between "the action executed" and "the requested outcome was
                # confirmed" is visible in the observations and in the final
                # response rather than being implied.
                context.add_observation(
                    Observation(
                        step_id=step.id,
                        content=self._verified_outcome_message(step),
                        success=True,
                        metadata={
                            "action": "verification_passed",
                            "step_id": step.id,
                            "verified": True,
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

    def _bind_notepad_target(self, step: PlanStep) -> str:
        """Bind and freeze the Notepad action target on a plan step.

        Guarantees, before permission evaluation and execution:

        * the application is explicitly ``Notepad`` (a step naming any other
          application is refused — Mamba has no capability to type into
          arbitrary applications);
        * the payload text is captured on the step, so the same text is what
          gets typed, observed, and verified;
        * the expected outcome predicate is pinned onto the step, so verification
          checks whether the *requested text* appeared rather than whether
          keystrokes were sent.

        Returns an error string when the target cannot be bound safely, or ""
        when the step is bound (or is not a cross-app step at all).
        """
        intent = step.intent.strip().lower()
        if intent not in _NOTEPAD_INTENTS:
            return ""

        if intent in _NOTEPAD_TEXT_INTENTS:
            app = _step_app_argument(step)
            if app and app.strip().lower() not in ("notepad", "notepad.exe"):
                return (
                    f"Refusing to type: the requested application '{app}' is not "
                    "supported. Mamba can only type into Notepad."
                )
            text = _step_text_argument(step)
            if not text:
                return (
                    "Refusing to type: the request did not specify any text to type."
                )
            step.metadata.setdefault("app", _NOTEPAD_APP)
            # Pin the payload used for typing and for verification.
            step.metadata["text"] = text
            expected = step.metadata.get("expected")
            if not isinstance(expected, dict) or _UI_TEXT_PREDICATE_KEY not in expected:
                step.metadata["expected"] = {
                    _UI_TEXT_PREDICATE_KEY: {"app": _NOTEPAD_APP, "text": text}
                }
            return ""

        step.metadata.setdefault("app", _NOTEPAD_APP)
        return ""

    def _execute_notepad_read(
        self,
        metadata: dict[str, Any],
    ) -> tuple[str | None, str]:
        """Read a bound Notepad window's text back through the desktop capability.

        Reuses the single existing desktop Notepad read path (the same tool the
        ``read_notepad_text`` skill uses) rather than adding a second mechanism.

        Returns (text, error). ``text`` is None when the text could not be read.
        """
        if not hasattr(self.executor, "handlers"):
            return None, "no desktop capability is wired to read Notepad text"
        handlers = getattr(self.executor, "handlers", None)
        if not isinstance(handlers, Mapping) or _UI_TEXT_READ_INTENT not in handlers:
            return None, "no desktop capability is wired to read Notepad text"

        target_meta = {
            key: metadata[key]
            for key in (
                "app",
                "hwnd",
                "target_title",
                "target_pid",
                "target_class",
                "target_process",
            )
            if metadata.get(key) is not None
        }
        read_input = TaskInput(
            step_id="ui-text-read",
            description="Read bound Notepad text for outcome verification",
            intent=_UI_TEXT_READ_INTENT,
            execution_id="verification",
            goal="verify Notepad outcome",
            step_metadata=target_meta,
        )
        try:
            output = handlers[_UI_TEXT_READ_INTENT].run(read_input, None)  # type: ignore[arg-type]
        except Exception as exc:
            return None, f"reading Notepad text for verification failed: {exc}"

        if not output.success:
            return None, output.content or "could not read the Notepad window's text"
        text = output.metadata.get("text")
        if not isinstance(text, str):
            return None, "Notepad text could not be observed"
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

        # Authoritative security classification from capability/tool metadata takes precedence
        risk_val = cap_meta.get("risk_level") or step.metadata.get("risk_level", RiskLevel.LOW)

        if isinstance(risk_val, str):
            try:
                risk_level = RiskLevel(risk_val.lower())
            except ValueError:
                risk_level = RiskLevel.LOW
        elif isinstance(risk_val, RiskLevel):
            risk_level = risk_val
        else:
            risk_level = RiskLevel.LOW

        merged_metadata = dict(step.metadata)
        for sensitive_key in ("destructive", "user_sensitive", "irreversible", "externally_visible"):
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
            approved = (
                step.metadata.get("approved") is True
                or context.request.metadata.get("approved") is True
            )
            if not approved:
                return False, perm_res.reason or f"Action '{step.description}' requires user confirmation", True

        return True, "", False

    def _resume_pending_approval(
        self,
        pending: PendingApproval,
        approval_request: UserRequest,
    ) -> ExecutionResult:
        """Resume execution of a paused step after user approval."""
        context = ExecutionContext.from_request(
            UserRequest(
                goal=pending.user_request.goal,
                metadata={**pending.user_request.metadata, "approved": True},
            )
        )
        context.transition_to(ExecutionState.PLANNING)
        context.attach_plan(pending.plan)
        context.transition_to(ExecutionState.EXECUTING)

        for obs in pending.context.observations:
            if obs.metadata.get("awaiting_approval"):
                continue
            context.add_observation(obs)

        pending.step.metadata["approved"] = True

        outcome, info = self._execute_step(context, pending.step)
        if outcome == _StepOutcome.FAILED:
            return context.record.to_result()

        remaining_steps = pending.plan.steps[pending.step_index + 1:]
        for rem_step in remaining_steps:
            outcome, rem_info = self._execute_step(context, rem_step)
            if outcome == _StepOutcome.AWAITING_APPROVAL:
                self._pending_approval = PendingApproval(
                    step=rem_step,
                    context=context,
                    plan=pending.plan,
                    step_index=pending.plan.steps.index(rem_step),
                    user_request=pending.user_request,
                    reason=rem_info,
                )
                prompt_msg = f"Action requires user confirmation: {rem_info}. Do you want to proceed?"
                obs = Observation(
                    step_id=rem_step.id,
                    content=prompt_msg,
                    success=False,
                    metadata={"permission_decision": "ask", "awaiting_approval": True},
                )
                context.add_observation(obs)
                context.mark_failed(prompt_msg)
                return context.record.to_result(output=prompt_msg)
            if outcome == _StepOutcome.FAILED:
                return context.record.to_result()

        if outcome == _StepOutcome.REPLAN or _plan_needs_replanning(pending.plan):
            return self._execution_loop(context, pending.user_request)

        self._update_memory(context, pending.user_request)
        context.transition_to(ExecutionState.COMPLETED)
        return context.record.to_result(output=_last_observation_content(context))

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
        if intent_lower in _NOTEPAD_TEXT_INTENTS:
            # Typing keystrokes is an action; the *outcome* must be observed.
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

        # ── Cross-app outcome verification ──
        # "Keystrokes were sent" is not evidence that the requested text landed
        # in the intended window. When a step's expected outcome is a UI text
        # predicate, the text is observed from the bound window after the fact
        # and the *existing* verifier decides VERIFIED / FAILED / INCONCLUSIVE.
        ui_predicate = expected.get(_UI_TEXT_PREDICATE_KEY) if isinstance(expected, dict) else None
        if ui_predicate is not None:
            return self._verify_ui_text(step, observation, ui_predicate)

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

    def _verify_ui_text(
        self,
        step: PlanStep,
        observation: Observation,
        predicate: Any,
    ) -> tuple[bool, str]:
        """Verify a cross-app outcome by reading the text back out of the window.

        Separates two distinct facts:

        * the action executed — the step's observation reports whether text was
          typed and whether the bound target was the active window; and
        * the requested outcome — the text actually present in the bound Notepad
          window, read back through the existing desktop capability.

        When the text cannot be observed (window gone, control unavailable, no
        desktop capability wired), the result is reported as INCONCLUSIVE rather
        than claimed as success.
        """
        if isinstance(predicate, dict):
            expected_text = str(predicate.get("text") or "")
            predicate_app = str(predicate.get("app") or _NOTEPAD_APP)
        else:
            expected_text = str(predicate or "")
            predicate_app = _NOTEPAD_APP

        if predicate_app.strip().lower() not in ("notepad", "notepad.exe"):
            return False, (
                f"verification failed: unsupported verification target "
                f"'{predicate_app}' (only Notepad is supported)"
            )

        if not expected_text:
            return False, "verification failed: no expected text was recorded for this step"

        metadata = {**dict(step.metadata), **dict(observation.metadata)}
        actual_text, read_error = self._execute_notepad_read(metadata)

        if actual_text is None:
            return False, (
                "verification inconclusive: the requested text could not be observed in "
                f"the intended Notepad window ({read_error})"
            )

        actual = {"ui_text": actual_text, "app": _NOTEPAD_APP}
        # Reuse the existing verifier predicate machinery: the observed window
        # text is compared with the framework's `contains` predicate rather than
        # a bespoke comparison.
        request = VerificationRequest(
            expected={"contains": expected_text},
            actual=actual,
            metadata={**metadata, "app": _NOTEPAD_APP},
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
                "verification failed: the text observed in the bound Notepad window was "
                f"{seen!r}, which does not contain {expected_text!r}"
            )
        return False, f"verification inconclusive: {result.reason}"

    def _verified_outcome_message(self, step: PlanStep) -> str:
        """Describe a verified outcome distinctly from a merely executed action."""
        expected = step.metadata.get("expected")
        if isinstance(expected, dict):
            ui_predicate = expected.get(_UI_TEXT_PREDICATE_KEY)
            if isinstance(ui_predicate, dict):
                text = str(ui_predicate.get("text") or "")
                app = str(ui_predicate.get("app") or _NOTEPAD_APP)
                return (
                    f"Verified: '{text}' is present in the {app} window "
                    f"(read back after typing)."
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
        # Cross-app Notepad actions are real, externally visible interactions.
        "launch_notepad", "open_notepad", "start_notepad",
        "type_text", "type_text_in_notepad", "type_in_notepad",
        "type_into_notepad", "write_in_notepad", "enter_text",
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
