"""Model routing implementation.

DefaultModelRouter implements Mamba's first real capability-aware routing
policy on top of the existing ModelProvider/ModelInfo abstraction:

    request -> explicit provider/model? -> required capability? ->
    multimodal compatible? -> available providers -> deterministic
    provider order (tie-break) -> ModelProvider

No new abstraction layer, provider-specific logic, or external routing
service is introduced. Everything here is derived from information the
existing ModelRequest / ModelInfo contracts already expose.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ModelRoutingError
from .protocols import ModelProvider
from .types import ModelInfo, ModelRequest, ModelResponse


# Total wall-time budget for the (rare) serial fallback across multiple
# candidate providers, so a dead primary cannot stack several 60s socket
# timeouts into a multi-minute stall. Env-tunable; per-provider socket
# timeouts still apply beneath it.
_TOTAL_TIMEOUT = float(os.environ.get("MAMBA_PROVIDER_TOTAL_TIMEOUT", "90"))

# HTTP status classification (Spec Part 5). Providers flatten the status into
# the exception message ("... HTTP 429 ..."); we read it back deterministically.
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_PERMANENT_STATUS = {400, 401, 403, 404, 405, 409, 413, 415, 422}
_STATUS_RE = re.compile(r"HTTP (\d{3})")
_TRANSIENT_WORDS = (
    "timed out", "timeout", "overloaded", "rate limit", "too many requests",
    "try again", "network error", "temporarily", "connection refused",
    "connection aborted", "reset by peer",
)
_PERMANENT_WORDS = (
    "invalid api key", "invalid_api_key", "unsupported model", "not found",
    "model not found", "invalid request", "permission denied", "unauthorized",
    "authentication", "bad request",
)


def _extract_status(exc: Exception) -> int | None:
    match = _STATUS_RE.search(str(exc))
    return int(match.group(1)) if match else None


def _classify_transient(exc: Exception) -> bool:
    """True if the failure is worth one bounded fallback to the next candidate.

    Permanent failures (bad key, unsupported model, invalid request) return
    False so we stop immediately instead of burning every provider's timeout.
    Cancellation is never transient. An uncertain provider error is treated as
    transient-but-bounded so a genuine blip still gets one fallback without ever
    hiding the original failure (Spec Part 5).
    """
    from core.cancellation import MambaCancelledError

    if isinstance(exc, MambaCancelledError):
        return False
    status = _extract_status(exc)
    if status is not None:
        if status in _TRANSIENT_STATUS:
            return True
        if status in _PERMANENT_STATUS:
            return False
    text = str(exc).lower()
    if any(word in text for word in _TRANSIENT_WORDS):
        return True
    if any(word in text for word in _PERMANENT_WORDS):
        return False
    return True

_MULTIMODAL_CAPABILITY_KEYS = ("multimodal", "image")


def _routing_hint(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelRoutingError(f"routing hint '{key}' must be a string")
    if not value.strip():
        return None
    return value


def _supports_multimodal(info: ModelInfo) -> bool:
    """Whether a provider's advertised capabilities cover image/multimodal input."""
    return any(bool(info.capabilities.get(key)) for key in _MULTIMODAL_CAPABILITY_KEYS)


def _provider_supports_streaming(info: ModelInfo) -> bool:
    """Whether a provider advertises streaming.

    Truthiness, not membership: an explicit ``{"streaming": False}`` must not
    be treated as capable.
    """
    return bool(info.capabilities.get("streaming"))


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """A routing decision paired with a human-readable, non-sensitive reason.

    Exposed as an explicit, optional entry point (`route_with_reason`) so
    callers that want explainability can get it, without changing the
    existing `ModelRouter.route(request) -> ModelProvider` contract that
    the rest of Mamba (PlanningAgent, Brain, AnalyzeSkill, screen skills,
    etc.) already depends on.
    """

    provider: ModelProvider
    reason: str


@dataclass(slots=True)
class DefaultModelRouter:
    """Selects a model provider for a request using deterministic, capability-aware routing.

    Routing priority (all derived from existing ModelRequest/ModelInfo data):

        1. Explicit provider requirement (request.metadata["provider"])
        2. Explicit model requirement (request.metadata["model"])
        3. Explicit capability requirement (request.metadata["capability"])
        4. Multimodal/image compatibility (request.has_images, or
           capability == "multimodal")
        5. Available providers satisfying the above
        6. Stable deterministic preference (existing provider order) as the
           final tie-breaker

    An explicit provider or model requirement that cannot be satisfied
    (unavailable, or incompatible with a required capability/multimodal
    input) fails honestly via ModelRoutingError rather than silently
    falling back to a different provider.
    """

    _providers: tuple[ModelProvider, ...]

    def __init__(self, providers: Sequence[ModelProvider]) -> None:
        self._providers = tuple(providers)

    def route(self, request: ModelRequest) -> ModelProvider:
        return self.route_with_reason(request).provider

    def route_candidates(self, request: ModelRequest) -> tuple[ModelProvider, ...]:
        """Resolve all registered providers satisfying the request in preference order."""
        if not self._providers:
            raise ModelRoutingError("no model providers are registered")

        metadata = request.metadata
        provider_hint = _routing_hint(metadata, "provider")
        model_hint = _routing_hint(metadata, "model")
        capability_hint = _routing_hint(metadata, "capability")
        needs_multimodal = request.has_images or capability_hint == "multimodal"

        # 1. Explicit provider requirement — validated, never silently rerouted.
        if provider_hint is not None:
            matches = self._matching_by_provider(provider_hint)
            if not matches:
                raise ModelRoutingError(f"no provider matching provider '{provider_hint}'")
            provider = matches[0]
            self._require_multimodal_if_needed(
                provider, needs_multimodal, f"explicitly requested provider '{provider_hint}'"
            )
            if (
                capability_hint is not None
                and capability_hint != "multimodal"
                and capability_hint not in self._provider_info(provider).capabilities
            ):
                raise ModelRoutingError(
                    f"explicitly requested provider '{provider_hint}' does not support "
                    f"required capability '{capability_hint}'"
                )
            return (provider,)

        # 2. Explicit model requirement — validated, never silently rerouted.
        if model_hint is not None:
            matches = self._matching_by_model(model_hint)
            if not matches:
                raise ModelRoutingError(f"no provider matching model '{model_hint}'")
            provider = matches[0]
            self._require_multimodal_if_needed(
                provider, needs_multimodal, f"explicitly requested model '{model_hint}'"
            )
            return (provider,)

        # 3. Explicit capability requirement narrows the candidate pool.
        candidates = self._providers
        if capability_hint is not None and capability_hint != "multimodal":
            candidates = self._matching_by_capability(capability_hint)
            if not candidates:
                raise ModelRoutingError(
                    f"no available provider matching capability '{capability_hint}'"
                )

        # 4. Multimodal/image compatibility narrows the candidate pool further.
        if needs_multimodal:
            multimodal_candidates = tuple(
                p for p in candidates if _supports_multimodal(self._provider_info(p))
            )
            if not multimodal_candidates:
                raise ModelRoutingError(
                    "no available provider supports required multimodal/image input"
                )
            candidates = multimodal_candidates

        if not candidates:
            raise ModelRoutingError("no available provider satisfies routing requirements")

        return candidates

    def route_with_reason(self, request: ModelRequest) -> RoutingDecision:
        """Resolve a provider for `request`, along with why it was chosen."""
        metadata = request.metadata
        provider_hint = _routing_hint(metadata, "provider")
        model_hint = _routing_hint(metadata, "model")
        capability_hint = _routing_hint(metadata, "capability")
        needs_multimodal = request.has_images or capability_hint == "multimodal"

        candidates = self.route_candidates(request)
        chosen = candidates[0]

        if provider_hint is not None:
            reason = f"explicit provider requirement: '{provider_hint}'"
        elif model_hint is not None:
            reason = f"explicit model requirement: '{model_hint}'"
        elif candidates == self._providers:
            reason = "no explicit requirement; default provider order"
        elif needs_multimodal:
            reason = "selected for required multimodal/image capability"
        else:
            reason = f"selected for required capability '{capability_hint}'"

        return RoutingDecision(chosen, reason)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Invoke a provider with bounded fallback among viable candidates.

        Cancellation is re-raised immediately and NEVER triggers a fallback to
        another provider (Spec Part 5). A permanent provider failure stops the
        loop at once; only transient failures advance to the next candidate, and
        the whole fallback is bounded by MAMBA_PROVIDER_TOTAL_TIMEOUT so a dead
        provider cannot stack multiple socket timeouts. The original failure is
        always surfaced (never masked) when nothing succeeds.
        """
        from core.cancellation import MambaCancelledError

        candidates = self.route_candidates(request)
        last_error: Exception | None = None
        last_response: ModelResponse | None = None
        attempts = 0
        deadline = time.monotonic() + _TOTAL_TIMEOUT if len(candidates) > 1 else None

        for i, provider in enumerate(candidates):
            attempts += 1
            try:
                response = provider.invoke(request)
            except MambaCancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if not _classify_transient(exc):
                    raise  # permanent: stop, do not burn the remaining candidates
                if deadline is not None and time.monotonic() >= deadline:
                    break  # bounded transient fallback
                continue

            if response.success:
                if i > 0:
                    meta = dict(response.metadata)
                    meta["fallback_from_primary"] = True
                    meta["fallback_attempt"] = i
                    meta["provider_attempts"] = attempts
                    meta["primary_provider_error_class"] = "transient"
                    return ModelResponse(
                        content=response.content,
                        provider=response.provider,
                        model=response.model,
                        success=True,
                        error=None,
                        metadata=meta,
                    )
                return response
            last_response = response

        if last_response is not None:
            return last_response
        if last_error is not None:
            raise last_error
        raise ModelRoutingError("all candidate model providers failed invocation")

    def stream(self, request: ModelRequest, on_text: Callable[[str], None]) -> ModelResponse:
        """Stream from the first viable candidate and return the complete response.

        Phase 3's fallback rules apply, plus one streaming-specific rule: once a
        provider has produced any output, a failure is re-raised instead of
        retried on another provider. Switching mid-answer would splice two
        providers' text into one reply, which is worse than failing. Cancellation
        is re-raised immediately and never falls back. A candidate that cannot
        stream degrades honestly to ``invoke()`` (the whole answer arrives as one
        delta) rather than being skipped or faked.
        """
        from core.cancellation import MambaCancelledError

        candidates = self.route_candidates(request)
        last_error: Exception | None = None
        last_response: ModelResponse | None = None
        deadline = time.monotonic() + _TOTAL_TIMEOUT if len(candidates) > 1 else None

        for provider in candidates:
            emitted = False

            def _tracked(delta: str, _cb=on_text) -> None:
                nonlocal emitted
                emitted = True
                _cb(delta)

            try:
                if (
                    _provider_supports_streaming(self._provider_info(provider))
                    and hasattr(provider, "stream")
                ):
                    return provider.stream(request, _tracked)
                response = provider.invoke(request)  # complete-path degrade
                if response.success:
                    on_text(response.content)
                    return response
                last_response = response
            except MambaCancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if emitted:
                    raise  # never switch providers after the first token
                if not _classify_transient(exc):
                    raise  # permanent: stop, do not burn the remaining candidates
                if deadline is not None and time.monotonic() >= deadline:
                    break  # bounded transient fallback
                continue

        if last_response is not None:
            return last_response
        if last_error is not None:
            raise last_error
        raise ModelRoutingError("all candidate model providers failed streaming")

    def _require_multimodal_if_needed(
        self, provider: ModelProvider, needs_multimodal: bool, subject: str
    ) -> None:
        if needs_multimodal and not _supports_multimodal(self._provider_info(provider)):
            raise ModelRoutingError(
                f"{subject} does not support required multimodal/image input"
            )

    def _matching_by_provider(self, provider_hint: str) -> tuple[ModelProvider, ...]:
        return tuple(
            provider
            for provider in self._providers
            if self._provider_info(provider).provider == provider_hint
        )

    def _matching_by_model(self, model_hint: str) -> tuple[ModelProvider, ...]:
        return tuple(
            provider
            for provider in self._providers
            if self._provider_info(provider).model == model_hint
        )

    def _matching_by_capability(self, capability_hint: str) -> tuple[ModelProvider, ...]:
        return tuple(
            provider
            for provider in self._providers
            if capability_hint in self._provider_info(provider).capabilities
        )

    def _select(
        self,
        matches: tuple[ModelProvider, ...],
        hint_description: str,
    ) -> ModelProvider:
        if not matches:
            raise ModelRoutingError(f"no provider matching {hint_description}")
        return matches[0]

    def _provider_info(self, provider: ModelProvider) -> ModelInfo:
        try:
            return provider.info
        except Exception as exc:
            raise ModelRoutingError(
                f"failed to read provider info: {exc}"
            ) from exc
