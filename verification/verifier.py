"""Default verifier implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import InvalidVerificationRequestError, VerificationEvaluationError
from .types import UNAVAILABLE, VerificationRequest, VerificationResult, VerificationStatus

_MAX_EVIDENCE_LENGTH = 200


def _is_unavailable(value: Any) -> bool:
    return value is UNAVAILABLE


def _compact_value(value: Any) -> Any:
    if _is_unavailable(value):
        return None

    if isinstance(value, (bool, int, float, str)) or value is None:
        return value

    if isinstance(value, (dict, list, tuple)):
        rendered = repr(value)
        if len(rendered) <= _MAX_EVIDENCE_LENGTH:
            return value
        return {
            "type": type(value).__name__,
            "length": len(value),
        }

    rendered = repr(value)
    if len(rendered) <= _MAX_EVIDENCE_LENGTH:
        return rendered

    return {
        "type": type(value).__name__,
        "repr": rendered[:_MAX_EVIDENCE_LENGTH],
    }


def _build_evidence(expected: Any, actual: Any) -> dict[str, Any]:
    return {
        "expected": _compact_value(expected),
        "actual": _compact_value(actual),
    }


@dataclass(slots=True)
class DefaultVerifier:
    """Deterministic verifier using Python equality semantics."""

    verifier_name: str = "default"

    def verify(self, request: VerificationRequest) -> VerificationResult:
        try:
            return self._verify(request)
        except InvalidVerificationRequestError:
            raise
        except Exception as exc:
            raise VerificationEvaluationError(str(exc)) from exc

    def _verify(self, request: VerificationRequest) -> VerificationResult:
        expected = request.expected
        actual = request.actual
        evidence = _build_evidence(expected, actual)
        metadata = {
            "verifier": self.verifier_name,
            **dict(request.metadata),
        }

        if request.metadata.get("insufficient_evidence") is True:
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                reason="insufficient evidence provided in request metadata",
                evidence=evidence,
                metadata=metadata,
            )

        if _is_unavailable(expected) or _is_unavailable(actual):
            missing = []
            if _is_unavailable(expected):
                missing.append("expected")
            if _is_unavailable(actual):
                missing.append("actual")
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                reason=f"{' and '.join(missing)} value is unavailable for comparison",
                evidence=evidence,
                metadata=metadata,
            )

        try:
            values_equal = expected == actual
        except Exception as exc:
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                reason=f"comparison could not be completed: {exc}",
                evidence=evidence,
                metadata=metadata,
            )

        if values_equal:
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                reason="expected and actual values are equal",
                evidence=evidence,
                metadata=metadata,
            )

        return VerificationResult(
            status=VerificationStatus.FAILED,
            reason="expected and actual values are not equal",
            evidence=evidence,
            metadata=metadata,
        )
