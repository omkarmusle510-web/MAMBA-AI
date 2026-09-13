"""Default verifier implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
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

        if _is_unavailable(actual):
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                reason="actual value is unavailable for comparison",
                evidence=evidence,
                metadata=metadata,
            )

        if _is_unavailable(expected):
            if request.metadata.get("verify") is True:
                if actual is not None and actual != "" and actual is not False:
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason="step produced non-empty valid output",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason="step produced empty or false output",
                    evidence=evidence,
                    metadata=metadata,
                )
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                reason="expected value is unavailable for comparison",
                evidence=evidence,
                metadata=metadata,
            )

        # Predicate dictionary support
        if isinstance(expected, dict):
            if "file_exists" in expected:
                target_path = Path(str(expected["file_exists"]))
                if target_path.exists():
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason=f"file '{target_path}' exists on filesystem",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason=f"file '{target_path}' does not exist on filesystem",
                    evidence=evidence,
                    metadata=metadata,
                )
            if "file_absent" in expected:
                target_path = Path(str(expected["file_absent"]))
                if not target_path.exists():
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason=f"file '{target_path}' is absent from filesystem",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason=f"file '{target_path}' still exists on filesystem",
                    evidence=evidence,
                    metadata=metadata,
                )
            if "content_matches" in expected:
                spec = expected["content_matches"]
                target_path = Path(str(spec.get("path", "")))
                expected_content = str(spec.get("content", ""))
                if not target_path.exists():
                    return VerificationResult(
                        status=VerificationStatus.FAILED,
                        reason=f"file '{target_path}' does not exist to verify content",
                        evidence=evidence,
                        metadata=metadata,
                    )
                try:
                    actual_content = target_path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    return VerificationResult(
                        status=VerificationStatus.FAILED,
                        reason=f"could not read '{target_path}' to verify content: {exc}",
                        evidence=evidence,
                        metadata=metadata,
                    )
                if actual_content == expected_content:
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason=f"file '{target_path}' content matches expected content",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason=f"file '{target_path}' content does not match expected content",
                    evidence=evidence,
                    metadata=metadata,
                )
            if "contains" in expected:
                substr = str(expected["contains"]).casefold()
                if substr in str(actual).casefold():
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason=f"actual value contains '{expected['contains']}'",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason=f"actual value does not contain '{expected['contains']}'",
                    evidence=evidence,
                    metadata=metadata,
                )
            if "not_contains" in expected:
                substr = str(expected["not_contains"]).casefold()
                if substr not in str(actual).casefold():
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason=f"actual value does not contain '{expected['not_contains']}'",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason=f"actual value contains '{expected['not_contains']}'",
                    evidence=evidence,
                    metadata=metadata,
                )
            if "pattern" in expected or "regex" in expected:
                pat = expected.get("pattern") or expected.get("regex")
                try:
                    if re.search(str(pat), str(actual)):
                        return VerificationResult(
                            status=VerificationStatus.VERIFIED,
                            reason=f"actual value matches pattern '{pat}'",
                            evidence=evidence,
                            metadata=metadata,
                        )
                    return VerificationResult(
                        status=VerificationStatus.FAILED,
                        reason=f"actual value does not match pattern '{pat}'",
                        evidence=evidence,
                        metadata=metadata,
                    )
                except re.error as exc:
                    return VerificationResult(
                        status=VerificationStatus.INCONCLUSIVE,
                        reason=f"invalid regex pattern: {exc}",
                        evidence=evidence,
                        metadata=metadata,
                    )
            if "exit_code" in expected:
                exp_code = expected["exit_code"]
                act_code = (
                    actual.get("exit_code")
                    if isinstance(actual, dict)
                    else (actual if isinstance(actual, int) else None)
                )
                if act_code == exp_code:
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        reason=f"exit code matches expected {exp_code}",
                        evidence=evidence,
                        metadata=metadata,
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    reason=f"exit code {act_code} does not match expected {exp_code}",
                    evidence=evidence,
                    metadata=metadata,
                )
            if "equals" in expected:
                if actual == expected["equals"]:
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

        if request.metadata.get("match_type") == "contains" or "contains" in request.metadata:
            target_str = str(request.metadata.get("contains") or expected).casefold()
            if target_str in str(actual).casefold():
                return VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    reason=f"actual value contains '{target_str}'",
                    evidence=evidence,
                    metadata=metadata,
                )
            return VerificationResult(
                status=VerificationStatus.FAILED,
                reason=f"actual value does not contain '{target_str}'",
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
