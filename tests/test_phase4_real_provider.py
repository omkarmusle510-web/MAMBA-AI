"""Opt-in Phase 4 validation against a real model provider.

Each test here performs ONE real (paid) provider call, so the module is
skipped unless both an explicit opt-in flag and a usable key are present —
running the normal suite must never consume provider traffic:

    MAMBA_REAL_PROVIDER_TESTS=1 pytest tests/test_phase4_real_provider.py -q
"""

from __future__ import annotations

import os

import pytest

from models.types import ModelRequest

_REAL_PROVIDER_TESTS = os.environ.get("MAMBA_REAL_PROVIDER_TESTS", "").strip() not in (
    "",
    "0",
    "false",
)
_HAS_KEY = bool(os.environ.get("GROQ_API_KEY", "").strip())
_HAS_GEMINI_KEY = bool(
    os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
)

_MONTHS_PROMPT = "List the twelve months of the year, one per line. No commentary."
_PARAMETERS = {"max_tokens": 256, "temperature": 0}


@pytest.mark.skipif(
    not (_REAL_PROVIDER_TESTS and _HAS_KEY),
    reason=(
        "one real (paid) provider call; set MAMBA_REAL_PROVIDER_TESTS=1 "
        "with GROQ_API_KEY present to run"
    ),
)
def test_groq_stream_matches_the_complete_response():
    """A real stream must reconstruct exactly the authoritative complete answer."""
    from models.providers.groq import GroqModelProvider

    provider = GroqModelProvider()
    assert provider.info.capabilities.get("streaming") is True

    request = ModelRequest(input=_MONTHS_PROMPT, parameters=_PARAMETERS)

    got: list[str] = []
    response = provider.stream(request, got.append)

    assert response.success is True
    assert response.metadata.get("streamed") is True
    assert got, "a real stream must deliver the answer as deltas"
    # The provisional deltas may never disagree with the authoritative result.
    # Chunk granularity itself is provider-controlled, so it is asserted by
    # the deterministic SSE/chunk unit tests, not against a paid call.
    assert "".join(got) == response.content


@pytest.mark.skipif(
    not (_REAL_PROVIDER_TESTS and _HAS_GEMINI_KEY),
    reason=(
        "one real (paid) provider call; set MAMBA_REAL_PROVIDER_TESTS=1 "
        "with GEMINI_API_KEY or GOOGLE_API_KEY present to run"
    ),
)
def test_gemini_stream_matches_the_complete_response():
    """The SDK chunk stream must reconstruct exactly the returned response."""
    from models.providers.gemini import GeminiModelProvider

    provider = GeminiModelProvider()
    if not provider.info.capabilities.get("streaming"):
        pytest.skip("installed Gemini client exposes no streaming surface")

    request = ModelRequest(input=_MONTHS_PROMPT, parameters=_PARAMETERS)

    got: list[str] = []
    response = provider.stream(request, got.append)

    assert response.success is True
    assert response.metadata.get("streamed") is True
    assert got, "a real stream must deliver the answer as deltas"
    assert "".join(got) == response.content

