"""Provider adapter boundary for concrete model implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ModelInfo, ModelRequest, ModelResponse


class BaseModelProvider(ABC):
    """Minimal common structure for provider adapters."""

    def __init__(self, info: ModelInfo) -> None:
        self._info = info

    @property
    def info(self) -> ModelInfo:
        return self._info

    @abstractmethod
    def invoke(self, request: ModelRequest) -> ModelResponse: ...
