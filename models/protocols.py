"""Contracts for model providers and routing."""

from __future__ import annotations

from typing import Protocol

from .types import ModelInfo, ModelRequest, ModelResponse


class ModelProvider(Protocol):
    """Adapter boundary for an external model provider.

    ``invoke`` is the only required call. Providers that advertise
    ``ModelInfo.capabilities["streaming"] = True`` additionally implement
    ``stream(request, on_text) -> ModelResponse``. It is deliberately not
    declared here as a protocol member: streaming is an optional presentation
    surface, and callers detect it structurally (``hasattr(provider, "stream")``)
    so existing invoke-only doubles keep satisfying this contract.
    """

    @property
    def info(self) -> ModelInfo: ...

    def invoke(self, request: ModelRequest) -> ModelResponse: ...


class ModelRouter(Protocol):
    """Future routing boundary for selecting a model provider."""

    def route(self, request: ModelRequest) -> ModelProvider: ...
