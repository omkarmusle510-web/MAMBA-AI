"""Normalized model request and response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One message in a model conversation input."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("role must not be empty")
        if not self.content.strip():
            raise ValueError("content must not be empty")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Normalized request to an AI model."""

    input: str | None = None
    messages: tuple[ModelMessage, ...] = ()
    system_instruction: str | None = None
    model_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        has_input = self.input is not None and self.input.strip() != ""
        has_messages = len(self.messages) > 0
        if not has_input and not has_messages:
            raise ValueError("either input or messages must be provided")


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Describes a model and provider capability."""

    provider: str
    model: str
    capabilities: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not self.model.strip():
            raise ValueError("model must not be empty")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized result from a model invocation."""

    content: str
    provider: str
    model: str
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
