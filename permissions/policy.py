"""Default permission policy implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import InvalidPermissionRequestError, PermissionEvaluationError
from .types import PermissionDecision, PermissionRequest, PermissionResult, RiskLevel

_DECISION_ORDER = {
    PermissionDecision.ALLOW: 0,
    PermissionDecision.ASK: 1,
    PermissionDecision.DENY: 2,
}

_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "destructive",
        "externally_visible",
        "user_sensitive",
        "irreversible",
    }
)

_BASE_DECISIONS: dict[RiskLevel, PermissionDecision] = {
    RiskLevel.LOW: PermissionDecision.ALLOW,
    RiskLevel.MEDIUM: PermissionDecision.ALLOW,
    RiskLevel.HIGH: PermissionDecision.ASK,
    RiskLevel.CRITICAL: PermissionDecision.DENY,
}


def _raise_decision(
    current: PermissionDecision,
    minimum: PermissionDecision,
) -> PermissionDecision:
    if _DECISION_ORDER[minimum] > _DECISION_ORDER[current]:
        return minimum
    return current


@dataclass(slots=True)
class DefaultPermissionPolicy:
    """Simple deterministic policy for tool execution permission."""

    policy_name: str = "default"

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        try:
            return self._evaluate(request)
        except InvalidPermissionRequestError:
            raise
        except ValueError as exc:
            raise InvalidPermissionRequestError(str(exc)) from exc
        except Exception as exc:
            raise PermissionEvaluationError(str(exc)) from exc

    def _evaluate(self, request: PermissionRequest) -> PermissionResult:
        decision = _BASE_DECISIONS[request.risk_level]
        reason_parts = [f"{request.risk_level.value} risk maps to {decision.value}"]
        result_metadata: dict[str, Any] = {
            "policy": self.policy_name,
            "base_decision": decision.value,
        }

        active_flags = self._active_sensitive_flags(request.metadata)
        if active_flags:
            decision, escalation_reasons = self._apply_metadata_escalation(
                decision,
                request.risk_level,
                active_flags,
            )
            reason_parts.extend(escalation_reasons)
            result_metadata["escalation_flags"] = list(active_flags)

        if request.reason:
            reason_parts.append(request.reason)

        return PermissionResult(
            decision=decision,
            reason="; ".join(reason_parts),
            request=request,
            metadata=result_metadata,
        )

    def _active_sensitive_flags(self, metadata: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            flag for flag in sorted(_SENSITIVE_METADATA_KEYS) if metadata.get(flag) is True
        )

    def _apply_metadata_escalation(
        self,
        decision: PermissionDecision,
        risk_level: RiskLevel,
        flags: tuple[str, ...],
    ) -> tuple[PermissionDecision, list[str]]:
        escalated = decision
        reasons: list[str] = []

        if "externally_visible" in flags:
            if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                escalated = _raise_decision(escalated, PermissionDecision.ASK)
                reasons.append("externally_visible requires user approval at high risk")

        if "user_sensitive" in flags:
            if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                escalated = _raise_decision(escalated, PermissionDecision.ASK)
                reasons.append("user_sensitive requires user approval at high risk")

        if "destructive" in flags:
            if risk_level == RiskLevel.CRITICAL:
                escalated = _raise_decision(escalated, PermissionDecision.DENY)
                reasons.append("destructive action denied at critical risk")
            elif risk_level == RiskLevel.HIGH:
                escalated = _raise_decision(escalated, PermissionDecision.ASK)
                reasons.append("destructive action requires user approval at high risk")

        if "irreversible" in flags:
            if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                escalated = _raise_decision(escalated, PermissionDecision.ASK)
                reasons.append("irreversible action requires user approval at high risk")

        return escalated, reasons
