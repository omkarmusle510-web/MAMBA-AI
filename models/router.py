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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ModelRoutingError
from .protocols import ModelProvider
from .types import ModelInfo, ModelRequest

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

    def route_with_reason(self, request: ModelRequest) -> RoutingDecision:
        """Resolve a provider for `request`, along with why it was chosen."""
        if not self._providers:
            raise ModelRoutingError("no model providers are registered")

        metadata = request.metadata
        provider_hint = _routing_hint(metadata, "provider")
        model_hint = _routing_hint(metadata, "model")
        capability_hint = _routing_hint(metadata, "capability")
        needs_multimodal = request.has_images or capability_hint == "multimodal"

        # 1. Explicit provider requirement — validated, never silently rerouted.
        if provider_hint is not None:
            provider = self._select(
                self._matching_by_provider(provider_hint),
                f"provider '{provider_hint}'",
            )
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
            return RoutingDecision(
                provider, f"explicit provider requirement: '{provider_hint}'"
            )

        # 2. Explicit model requirement — validated, never silently rerouted.
        if model_hint is not None:
            provider = self._select(
                self._matching_by_model(model_hint),
                f"model '{model_hint}'",
            )
            self._require_multimodal_if_needed(
                provider, needs_multimodal, f"explicitly requested model '{model_hint}'"
            )
            return RoutingDecision(provider, f"explicit model requirement: '{model_hint}'")

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

        # 5/6. Best available provider; existing provider order is the tie-break.
        chosen = candidates[0]
        if candidates == self._providers:
            reason = "no explicit requirement; default provider order"
        elif needs_multimodal:
            reason = "selected for required multimodal/image capability"
        else:
            reason = f"selected for required capability '{capability_hint}'"
        return RoutingDecision(chosen, reason)

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
