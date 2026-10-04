"""Provider adapter boundary for concrete model implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from .errors import ModelProviderError
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

    def stream(
        self, request: ModelRequest, on_text: Callable[[str], None]
    ) -> ModelResponse:
        """Stream a response, pushing ordered text deltas to ``on_text``.

        Optional surface: providers that cannot stream do NOT override this and
        MUST NOT set ``ModelInfo.capabilities["streaming"]``, so callers fall
        back to ``invoke()`` and the complete-response path stays valid.
        A streaming provider MUST return the complete ``ModelResponse`` in
        addition to the deltas — deltas are provisional, the returned response
        is authoritative.
        """
        raise ModelProviderError(
            f"{self._info.provider} provider does not support streaming"
        )
