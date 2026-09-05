"""Mamba Brain - Intelligent observation-driven execution coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memory.protocols import MemoryStore
from memory.types import MemoryEntry, MemoryQuery
from models.protocols import ModelProvider, ModelRouter
from models.types import ModelRequest
from permissions.protocols import PermissionPolicy
from permissions.types import PermissionDecision, PermissionRequest, RiskLevel
from verification.protocols import Verifier
from verification.types import UNAVAILABLE, VerificationRequest, VerificationStatus

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
    return observations[-1].content


def _plan_needs_replanning(plan: ExecutionPlan) -> bool:
    return plan.metadata.get(_NEEDS_REPLANNING_KEY) is True


def _plan_signature(plan: ExecutionPlan) -> tuple[tuple[str, str], ...]:
    """A cheap fingerprint used to detect a planner repeating itself."""
    return tuple(
        (step.intent.strip().lower(), step.description.strip())
        for step in plan.steps
    )


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

    def run(self, request: str | UserRequest) -> ExecutionResult:
        """Run a user request through the observation-driven execution lifecycle."""

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

        # ── 2. Context Assembly ──
        context = ExecutionContext.from_request(user_request)

        # ── 3. Memory Retrieval ──
        if not self._retrieve_memory(context, user_request):
            return context.record.to_result()

        # ── 4. Observation-Driven Execution Loop ──
        return self._execution_loop(context, user_request)

    def route_model(self, request: ModelRequest) -> ModelProvider | None:
        """Route a model request through the model router if configured."""
        if self.model_router is not None:
            return self.model_router.route(request)
        return None

    # ── Private Implementation ──

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
        """Query memory store and attach relevant memories to context.

        Returns True if successful (or no memory configured), False on failure.
        """
        if self.memory is None:
            return True
        try:
            query = MemoryQuery(query=user_request.goal)
            result = self.memory.retrieve(query)
            if result.entries:
                context.record.request.metadata["retrieved_memories"] = [
                    entry.content for entry in result.entries
                ]
        except Exception as exc:
            context.mark_failed(f"memory retrieval failed: {exc}")
            return False
        return True

    def _execution_loop(
        self, context: ExecutionContext, user_request: UserRequest,
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
        previous_signature: tuple[tuple[str, str], ...] | None = None

        while cycles_used < self.max_cycles:
            cycles_used += 1

            # ── Reason / Plan ──
            plan = self._reason(context)
            if plan is None:
                return context.record.to_result()

            # ── Loop-prevention: a planner repeating an identical plan is
            # making no progress; stop instead of burning further cycles. ──
            signature = _plan_signature(plan)
            if signature == previous_signature:
                context.mark_failed(
                    "replanning produced an identical plan with no new "
                    "information; stopping to avoid a pointless loop"
                )
                return context.record.to_result()
            previous_signature = signature

            # ── Execute plan steps ──
            outcome = self._execute_plan(context, plan)

            if outcome == _StepOutcome.FAILED:
                # Context already marked failed
                return context.record.to_result()

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
        self, context: ExecutionContext, plan: ExecutionPlan,
    ) -> _StepOutcome:
        """Execute steps from a plan, evaluating each observation.

        A step's own observation can request replanning mid-plan (e.g. a
        failed verification flagged for retry). Once every step in the plan
        has executed successfully, the plan completes (FINISHED) unless the
        planner itself flagged, via `plan.metadata["needs_replanning"]`,
        that it already expects those steps to surface information it
        needs before it can continue - in which case control returns to
        the caller as REPLAN so a new cycle can reason over the results
        just observed.

        Returns the overall outcome: FINISHED, REPLAN, or FAILED.
        """
        for step in plan.steps:
            outcome = self._execute_step(context, step)
            if outcome != _StepOutcome.FINISHED:
                return outcome

        if _plan_needs_replanning(plan):
            return _StepOutcome.REPLAN

        return _StepOutcome.FINISHED

    def _execute_step(
        self, context: ExecutionContext, step: PlanStep,
    ) -> _StepOutcome:
        """Execute a single step through the full permission → execute → observe → verify pipeline.

        Returns FINISHED to continue to the next step, REPLAN to trigger
        a new reasoning cycle, or FAILED to halt execution.
        """
        # ── Permission ──
        if self.permissions is not None:
            allowed, reason = self._evaluate_permission(step, context)
            if not allowed:
                obs = Observation(
                    step_id=step.id,
                    content=reason,
                    success=False,
                    metadata={"permission_decision": "denied"},
                )
                context.add_observation(obs)
                context.mark_failed(reason)
                return _StepOutcome.FAILED

        # ── Execute ──
        try:
            observation = self.executor.execute(step, context)
            context.add_observation(observation)
        except CoreError as exc:
            obs = Observation(step_id=step.id, content=str(exc), success=False)
            context.add_observation(obs)
            context.mark_failed(str(exc))
            return _StepOutcome.FAILED
        except Exception as exc:
            obs = Observation(step_id=step.id, content=str(exc), success=False)
            context.add_observation(obs)
            context.mark_failed(f"execution error: {exc}")
            return _StepOutcome.FAILED

        # ── Evaluate Observation ──
        if not observation.success:
            # The action failed. Check if re-planning is appropriate.
            if observation.metadata.get("replan") is True:
                return _StepOutcome.REPLAN
            context.mark_failed(observation.content or "step execution failed")
            return _StepOutcome.FAILED

        if observation.metadata.get("replan") is True:
            # Action succeeded but indicates the plan should be reconsidered
            # (e.g. discovered new information that changes the approach).
            return _StepOutcome.REPLAN

        # ── Verification ──
        if self.verifier is not None and self._needs_verification(step, observation):
            verified, reason = self._verify(step, observation)
            if not verified:
                # Verification failure may trigger re-planning when the step
                # metadata explicitly requests it.
                if step.metadata.get("replan_on_verification_failure") is True:
                    return _StepOutcome.REPLAN
                context.mark_failed(reason)
                return _StepOutcome.FAILED

        return _StepOutcome.FINISHED

    def _evaluate_permission(
        self, step: PlanStep, context: ExecutionContext,
    ) -> tuple[bool, str]:
        """Evaluate permissions for a plan step."""
        if self.permissions is None:
            return True, ""

        action = step.metadata.get("action") or step.intent or "execute"
        tool_name = (
            step.metadata.get("tool_name")
            or step.metadata.get("capability")
            or "step"
        )
        risk_val = step.metadata.get("risk_level", RiskLevel.LOW)

        if isinstance(risk_val, str):
            try:
                risk_level = RiskLevel(risk_val.lower())
            except ValueError:
                risk_level = RiskLevel.LOW
        elif isinstance(risk_val, RiskLevel):
            risk_level = risk_val
        else:
            risk_level = RiskLevel.LOW

        perm_req = PermissionRequest(
            action=action,
            tool_name=tool_name,
            risk_level=risk_level,
            resource=step.metadata.get("resource"),
            reason=step.description,
            metadata=dict(step.metadata),
        )

        try:
            perm_res = self.permissions.evaluate(perm_req)
        except Exception as exc:
            return False, f"permission evaluation failed: {exc}"

        if perm_res.decision == PermissionDecision.DENY:
            return False, f"permission denied: {perm_res.reason}"

        if perm_res.decision == PermissionDecision.ASK:
            approved = (
                step.metadata.get("approved") is True
                or context.request.metadata.get("approved") is True
            )
            if not approved:
                return False, f"action requires user approval: {perm_res.reason}"

        return True, ""

    def _needs_verification(
        self, step: PlanStep, observation: Observation,
    ) -> bool:
        """Determine whether verification is applicable for a step."""
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
        actual = observation.metadata.get("actual", observation.content)

        v_req = VerificationRequest(
            expected=expected,
            actual=actual,
            metadata=dict(step.metadata),
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

    def _update_memory(
        self, context: ExecutionContext, user_request: UserRequest,
    ) -> None:
        """Store execution outcome in memory. Failures are silently absorbed."""
        if self.memory is None:
            return
        try:
            last_content = _last_observation_content(context)
            self.memory.store(
                MemoryEntry(
                    content=f"Goal: {user_request.goal} -> Outcome: {last_content}",
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


def create_brain(
    planner: Planner,
    executor: Executor,
    *,
    memory: MemoryStore | None = None,
    permissions: PermissionPolicy | None = None,
    verifier: Verifier | None = None,
    model_router: ModelRouter | None = None,
    max_cycles: int = _DEFAULT_MAX_CYCLES,
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
    )
