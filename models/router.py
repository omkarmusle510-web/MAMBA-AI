"""Model routing implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ModelRoutingError
from .protocols import ModelProvider
from .types import ModelInfo, ModelRequest


def _routing_hint(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelRoutingError(f"routing hint '{key}' must be a string")
    if not value.strip():
        return None
    return value


@dataclass(slots=True)
class DefaultModelRouter:
    """Selects a model provider for a request using deterministic routing hints."""

    _providers: tuple[ModelProvider, ...]

    def __init__(self, providers: Sequence[ModelProvider]) -> None:
        self._providers = tuple(providers)

    def route(self, request: ModelRequest) -> ModelProvider:
        if not self._providers:
            raise ModelRoutingError("no model providers are registered")

        metadata = request.metadata
        provider_hint = _routing_hint(metadata, "provider")
        model_hint = _routing_hint(metadata, "model")
        capability_hint = _routing_hint(metadata, "capability")

        if provider_hint is not None:
            return self._select(
                self._matching_by_provider(provider_hint),
                f"provider '{provider_hint}'",
            )

        if model_hint is not None:
            return self._select(
                self._matching_by_model(model_hint),
                f"model '{model_hint}'",
            )

        if capability_hint is not None:
            return self._select(
                self._matching_by_capability(capability_hint),
                f"capability '{capability_hint}'",
            )

        return self._providers[0]

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
