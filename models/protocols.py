"""Contracts for model providers and routing."""

from __future__ import annotations

from typing import Protocol

from .types import ModelInfo, ModelRequest, ModelResponse


class ModelProvider(Protocol):
    """Adapter boundary for an external model provider."""

    @property
    def info(self) -> ModelInfo: ...

    def invoke(self, request: ModelRequest) -> ModelResponse: ...


class ModelRouter(Protocol):
    """Future routing boundary for selecting a model provider."""

    def route(self, request: ModelRequest) -> ModelProvider: ...
