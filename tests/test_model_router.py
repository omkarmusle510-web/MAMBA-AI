"""Focused tests for DefaultModelRouter's capability-aware routing policy.

Uses lightweight fake providers (no real API calls) since provider-level
behavior (NVIDIA/Groq/Gemini HTTP calls) is already validated elsewhere and
must not be re-exercised here.
"""

from __future__ import annotations

import pytest

from models.errors import ModelRoutingError
from models.router import DefaultModelRouter
from models.types import ModelImagePart, ModelInfo, ModelMessage, ModelRequest, ModelResponse


class FakeProvider:
    """Minimal stand-in satisfying the ModelProvider protocol."""

    def __init__(self, provider: str, model: str, capabilities: dict | None = None) -> None:
        self._info = ModelInfo(provider=provider, model=model, capabilities=capabilities or {})

    @property
    def info(self) -> ModelInfo:
        return self._info

    def invoke(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover - not exercised
        return ModelResponse(content="fake", provider=self._info.provider, model=self._info.model)


def _text_request(**metadata) -> ModelRequest:
    return ModelRequest(input="hello", metadata=metadata)


def _image_request(**metadata) -> ModelRequest:
    return ModelRequest(
        messages=(
            ModelMessage(
                role="user",
                content=(ModelImagePart(data=b"\x89PNG", media_type="image/png"),),
            ),
        ),
        metadata=metadata,
    )


@pytest.fixture()
def providers():
    nvidia_text = FakeProvider("nvidia", "nvidia/nemotron-3-super-120b-a12b", {"chat": True, "text_generation": True})
    nvidia_vision = FakeProvider(
        "nvidia", "meta/llama-3.2-11b-vision-instruct",
        {"chat": True, "text_generation": True, "multimodal": True, "image": True},
    )
    groq = FakeProvider("groq", "qwen/qwen3.8-27b", {"chat": True, "text_generation": True})
    gemini = FakeProvider(
        "gemini", "gemini-2.5-flash",
        {"chat": True, "text_generation": True, "multimodal": True, "image": True},
    )
    return {"nvidia_text": nvidia_text, "nvidia_vision": nvidia_vision, "groq": groq, "gemini": gemini}


def test_explicit_groq_provider_request(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"], providers["gemini"]])
    chosen = router.route(_text_request(provider="groq"))
    assert chosen is providers["groq"]


def test_explicit_gemini_provider_request(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"], providers["gemini"]])
    chosen = router.route(_text_request(provider="gemini"))
    assert chosen is providers["gemini"]


def test_explicit_nvidia_provider_request(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"], providers["gemini"]])
    chosen = router.route(_text_request(provider="nvidia"))
    assert chosen is providers["nvidia_text"]


def test_multimodal_request_cannot_select_text_only_provider(providers):
    # Only text-capable providers available -> must fail honestly, not silently degrade.
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"]])
    with pytest.raises(ModelRoutingError):
        router.route(_image_request())


def test_multimodal_request_selects_capable_provider_when_no_explicit_provider(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"], providers["gemini"]])
    chosen = router.route(_image_request())
    assert chosen is providers["gemini"]


def test_explicit_provider_incompatible_with_multimodal_request_fails_honestly(providers):
    # Explicitly requested Groq (text-only) for an image request must not silently
    # reroute to Gemini/NVIDIA-vision — it must fail.
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"], providers["gemini"]])
    with pytest.raises(ModelRoutingError):
        router.route(_image_request(provider="groq"))


def test_missing_provider_is_handled_correctly(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"]])
    with pytest.raises(ModelRoutingError):
        router.route(_text_request(provider="anthropic"))


def test_no_explicit_provider_uses_deterministic_default_order(providers):
    router = DefaultModelRouter([providers["groq"], providers["nvidia_text"], providers["gemini"]])
    chosen = router.route(_text_request())
    assert chosen is providers["groq"]


def test_capability_hint_still_supported(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["nvidia_vision"]])
    chosen = router.route(_text_request(capability="multimodal"))
    assert chosen is providers["nvidia_vision"]


def test_route_with_reason_exposes_explainable_decision(providers):
    router = DefaultModelRouter([providers["nvidia_text"], providers["groq"], providers["gemini"]])
    decision = router.route_with_reason(_text_request(provider="gemini"))
    assert decision.provider is providers["gemini"]
    assert "gemini" in decision.reason


def test_no_providers_registered_raises():
    router = DefaultModelRouter([])
    with pytest.raises(ModelRoutingError):
        router.route(_text_request())
