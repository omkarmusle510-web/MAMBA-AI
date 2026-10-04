"""Phase 4 streaming tests (sink, providers, router, analyze, brain, transport)."""
from __future__ import annotations

import urllib.error

import pytest

import core.streaming as streaming
from core.brain import Brain
from core.cancellation import CancellationToken, MambaCancelledError
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus, UserRequest
from models.errors import ModelProviderError
from models.provider import BaseModelProvider
from models.providers._openai_sse import DONE, parse_sse_delta
from models.router import DefaultModelRouter, _provider_supports_streaming
from models.types import ModelInfo, ModelRequest, ModelResponse
from skills.analyze import AnalyzeSkill
from skills.types import SkillInput
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tests.test_core_lifecycle import StaticPlanner
from tests.test_model_router import FakeProvider


def test_emit_token_delivers_to_bound_sink():
    received: list[str] = []
    token = streaming.set_current_sink(received.append)
    try:
        assert streaming.emit_token("Hello") is True
        assert streaming.emit_token(" world") is True
    finally:
        streaming.reset_current_sink(token)
    assert received == ["Hello", " world"]


def test_emit_token_noop_without_sink():
    assert streaming.current_sink() is None
    assert streaming.emit_token("ignored") is False


def test_reset_restores_previous_sink():
    outer: list[str] = []
    tok_out = streaming.set_current_sink(outer.append)
    inner: list[str] = []
    tok_in = streaming.set_current_sink(inner.append)
    streaming.emit_token("a")
    streaming.reset_current_sink(tok_in)
    streaming.emit_token("b")
    streaming.reset_current_sink(tok_out)
    assert inner == ["a"]
    assert outer == ["b"]


# ── Task 2: provider streaming contract + capability detection ──


class _InvokeOnly(BaseModelProvider):
    def invoke(self, request):
        return ModelResponse(content="ok", provider="x", model="m")


def test_default_stream_raises():
    p = _InvokeOnly(ModelInfo(provider="x", model="m"))
    with pytest.raises(ModelProviderError):
        p.stream(ModelRequest(input="hi"), lambda t: None)


def test_capability_detection_is_truthiness_not_membership():
    assert _provider_supports_streaming(
        ModelInfo(provider="x", model="m", capabilities={"streaming": True})
    ) is True
    # {"streaming": False} must NOT be treated as capable (membership bug class).
    assert _provider_supports_streaming(
        ModelInfo(provider="x", model="m", capabilities={"streaming": False})
    ) is False
    assert _provider_supports_streaming(
        ModelInfo(provider="x", model="m", capabilities={})
    ) is False


# ── Task 3: Gemini provider streaming ──

from models.providers.gemini import GeminiModelProvider


class _Chunk:
    def __init__(self, text):
        self.text = text


class _FakeGeminiModels:
    """Mimics the google-genai ``models`` surface used by invoke()/stream()."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.stream_kwargs: dict | None = None

    def generate_content(self, **kwargs):
        return _Chunk("".join(c.text for c in self._chunks))

    def generate_content_stream(self, **kwargs):
        self.stream_kwargs = kwargs
        return iter(self._chunks)


class _FakeGeminiClient:
    def __init__(self, chunks):
        self.models = _FakeGeminiModels(chunks)


def test_gemini_stream_emits_ordered_chunks_and_returns_full_response():
    chunks = [_Chunk("Hel"), _Chunk("lo "), _Chunk("there")]
    client = _FakeGeminiClient(chunks)
    p = GeminiModelProvider(api_key="k", _client=client)
    assert p.info.capabilities.get("streaming") is True

    seen: list[str] = []
    resp = p.stream(ModelRequest(input="hi"), seen.append)

    assert seen == ["Hel", "lo ", "there"]
    assert resp.success is True
    assert resp.content == "Hello there"  # complete, authoritative
    assert resp.provider == "gemini"
    # stream() must reuse invoke()'s request construction, not invent its own.
    assert client.models.stream_kwargs is not None
    assert set(client.models.stream_kwargs) >= {"model", "contents"}


def test_gemini_stream_failure_midway_surfaces_partial_then_error():
    class _BoomModels(_FakeGeminiModels):
        def generate_content_stream(self, **kwargs):
            def gen():
                yield _Chunk("part")
                raise RuntimeError("HTTP 503 upstream boom")
            return gen()

    client = _FakeGeminiClient([])
    client.models = _BoomModels([])
    p = GeminiModelProvider(api_key="k", _client=client)

    got: list[str] = []
    with pytest.raises(ModelProviderError) as ei:
        p.stream(ModelRequest(input="hi"), got.append)

    assert got == ["part"]  # partial emitted, then surfaced (never masked)
    assert "503" in str(ei.value)  # router._classify_transient depends on this


def test_gemini_without_stream_surface_degrades_to_invoke():
    """IMPORTANT DESIGN RULE: a non-streaming provider still answers completely."""

    class _NoStreamModels:
        def __init__(self, chunks):
            self._chunks = chunks

        def generate_content(self, **kwargs):
            return _Chunk("".join(c.text for c in self._chunks))

    class _NoStreamClient:
        def __init__(self, chunks):
            self.models = _NoStreamModels(chunks)

    p = GeminiModelProvider(api_key="k", _client=_NoStreamClient([_Chunk("whole answer")]))
    assert p.info.capabilities.get("streaming") is False

    got: list[str] = []
    resp = p.stream(ModelRequest(input="hi"), got.append)
    assert resp.content == "whole answer" and resp.success is True
    assert got == []  # no fake streaming


# ── Task 4: Groq + NVIDIA streaming through the shared SSE parser ──


def test_parse_sse_delta_extracts_content():
    assert parse_sse_delta('data: {"choices":[{"delta":{"content":"Hi"}}]}') == "Hi"
    assert parse_sse_delta('data: {"choices":[{"delta":{}}]}') is None  # role-only line
    assert parse_sse_delta("") is None  # keep-alive
    assert parse_sse_delta(": ping") is None  # comment
    assert parse_sse_delta("data: [DONE]") == DONE


def _fake_sse_http(lines):
    def _http_stream(*, url, headers, body, timeout):
        assert body.get("stream") is True
        assert headers.get("Accept") == "text/event-stream"
        return iter(lines)
    return _http_stream


def test_groq_stream_emits_chunks_and_returns_full():
    from models.providers.groq import GroqModelProvider

    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}],"model":"qwen/x"}',
        'data: {"choices":[{"delta":{"content":"lo"}}],"model":"qwen/x"}',
        "data: [DONE]",
    ]
    p = GroqModelProvider(api_key="k", _http_stream=_fake_sse_http(lines))
    assert p.info.capabilities.get("streaming") is True

    got: list[str] = []
    resp = p.stream(ModelRequest(input="hi"), got.append)
    assert got == ["Hel", "lo"]
    assert resp.content == "Hello" and resp.success is True and resp.provider == "groq"


def test_groq_stream_error_body_preserves_http_status_for_router():
    from models.providers.groq import GroqModelProvider

    def _boom(*, url, headers, body, timeout):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

    p = GroqModelProvider(api_key="k", _http_stream=_boom)
    with pytest.raises(ModelProviderError) as ei:
        p.stream(ModelRequest(input="hi"), lambda t: None)
    # router._classify_transient() matches "HTTP <code>".
    assert "HTTP 429" in str(ei.value)


def test_nvidia_stream_emits_chunks_and_returns_full():
    from models.providers.nvidia import NVIDIAModelProvider

    lines = [
        'data: {"choices":[{"delta":{"content":"All "}}]}',
        'data: {"choices":[{"delta":{"content":"good"}}]}',
        "data: [DONE]",
    ]
    p = NVIDIAModelProvider(api_key="k", _http_stream=_fake_sse_http(lines))
    assert p.info.capabilities.get("streaming") is True

    got: list[str] = []
    resp = p.stream(ModelRequest(input="hi"), got.append)
    assert got == ["All ", "good"]
    assert resp.content == "All good" and resp.provider == "nvidia"


def test_nvidia_stream_keeps_reasoning_out_of_deltas_but_usable_as_answer():
    """Reasoning tokens are never shown; invoke() parity for the final answer."""
    from models.providers.nvidia import NVIDIAModelProvider

    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"secret chain"}}]}',
        'data: {"choices":[{"delta":{"content":"the answer"}}]}',
        "data: [DONE]",
    ]
    p = NVIDIAModelProvider(api_key="k", _http_stream=_fake_sse_http(lines))
    got: list[str] = []
    resp = p.stream(ModelRequest(input="hi"), got.append)
    assert got == ["the answer"]  # internal reasoning is not streamed to the UI
    assert resp.content == "the answer"

    reasoning_only = [
        'data: {"choices":[{"delta":{"reasoning_content":"only thinking"}}]}',
        "data: [DONE]",
    ]
    p2 = NVIDIAModelProvider(api_key="k", _http_stream=_fake_sse_http(reasoning_only))
    got2: list[str] = []
    resp2 = p2.stream(ModelRequest(input="hi"), got2.append)
    assert got2 == []
    assert resp2.content == "only thinking"  # same fallback invoke() applies


# ── Task 5: router streaming with the first-token fallback gate ──


class _StreamProvider(FakeProvider):
    """FakeProvider that emits ``fail_after`` chunks and then raises, or streams all."""

    def __init__(self, provider, model, chunks, fail_after=None, exc=None):
        super().__init__(provider, model, capabilities={"chat": True, "streaming": True})
        self._chunks = list(chunks)
        self._fail_after = fail_after
        self._exc = exc
        self.stream_calls = 0

    def stream(self, request, on_text):
        self.stream_calls += 1
        limit = (
            len(self._chunks) if self._fail_after is None
            else min(self._fail_after, len(self._chunks))
        )
        for chunk in self._chunks[:limit]:
            on_text(chunk)
        if self._fail_after is not None:
            raise self._exc
        return ModelResponse(
            content="".join(self._chunks),
            provider=self._info.provider,
            model=self._info.model,
        )


def _stream_req() -> ModelRequest:
    return ModelRequest(input="hi")


def test_stream_emits_all_chunks_then_complete():
    got: list[str] = []
    r = DefaultModelRouter([_StreamProvider("groq", "m", ["A", "B", "C"])])
    resp = r.stream(_stream_req(), got.append)
    assert got == ["A", "B", "C"]
    assert resp.content == "ABC"


def test_stream_failure_before_first_token_falls_back_bounded():
    got: list[str] = []
    bad = _StreamProvider("a", "m", [], fail_after=0, exc=ModelProviderError("HTTP 503 boom"))
    good = _StreamProvider("b", "m", ["X"])
    r = DefaultModelRouter([bad, good])
    resp = r.stream(_stream_req(), got.append)
    assert resp.provider == "b" and got == ["X"]


def test_stream_failure_after_first_token_does_not_switch_providers():
    got: list[str] = []
    bad = _StreamProvider("a", "m", ["par"], fail_after=1, exc=ModelProviderError("HTTP 503 boom"))
    never = _StreamProvider("b", "m", ["SHOULD_NOT_APPEAR"])
    r = DefaultModelRouter([bad, never])
    with pytest.raises(ModelProviderError):
        r.stream(_stream_req(), got.append)
    assert got == ["par"]  # partial shown, then deterministic failure
    assert "SHOULD_NOT_APPEAR" not in got
    assert never.stream_calls == 0  # provider B never started


def test_stream_permanent_failure_before_first_token_stops():
    got: list[str] = []
    bad = _StreamProvider("a", "m", [], fail_after=0, exc=ModelProviderError("HTTP 401 invalid api key"))
    backup = _StreamProvider("b", "m", ["Y"])
    r = DefaultModelRouter([bad, backup])
    with pytest.raises(ModelProviderError):
        r.stream(_stream_req(), got.append)
    assert backup.stream_calls == 0
    assert got == []


def test_stream_cancellation_never_triggers_fallback():
    cancelled = _StreamProvider("a", "m", ["x"], fail_after=1, exc=MambaCancelledError())
    backup = _StreamProvider("b", "m", ["y"])
    r = DefaultModelRouter([cancelled, backup])
    with pytest.raises(MambaCancelledError):
        r.stream(_stream_req(), lambda t: None)
    assert backup.stream_calls == 0


def test_stream_non_streaming_provider_degrades_to_complete():
    got: list[str] = []
    r = DefaultModelRouter([FakeProvider("groq", "m", capabilities={"chat": True})])
    resp = r.stream(_stream_req(), got.append)
    assert resp.success and got == [resp.content]  # whole content delivered once


# ── Task 6: AnalyzeSkill answer-path attach ──


class _StreamRouter:
    def __init__(self):
        self.stream_calls = 0
        self.invoke_calls = 0

    def invoke(self, request):
        self.invoke_calls += 1
        return ModelResponse(content="full answer", provider="groq", model="m")

    def stream(self, request, on_text):
        self.stream_calls += 1
        for delta in ("full ", "answer"):
            on_text(delta)
        return ModelResponse(content="full answer", provider="groq", model="m")


def _skill_input(goal="explain my project"):
    ur = UserRequest(goal=goal)
    ctx = ExecutionContext.from_request(ur)
    ti = TaskInput(
        step_id="s1",
        description=goal,
        intent="explain",
        execution_id=ctx.execution_id,
        goal=goal,
        step_metadata={},
    )
    return SkillInput.from_task(ti, ctx)


def test_analyze_uses_stream_when_sink_bound():
    router = _StreamRouter()
    skill = AnalyzeSkill(model_router=router)
    got: list[str] = []
    tok = streaming.set_current_sink(got.append)
    try:
        out = skill.execute(_skill_input())
    finally:
        streaming.reset_current_sink(tok)

    assert out.success and out.content == "full answer"
    assert router.stream_calls == 1 and router.invoke_calls == 0
    assert got == ["full ", "answer"]


def test_analyze_uses_invoke_when_no_sink():
    router = _StreamRouter()
    skill = AnalyzeSkill(model_router=router)
    out = skill.execute(_skill_input())
    assert out.success and out.content == "full answer"
    assert router.invoke_calls == 1 and router.stream_calls == 0


def test_analyze_surfaces_stream_failure_as_unsuccessful_output():
    """A mid-stream failure must not be reported as a completed answer."""

    class _BoomRouter:
        def stream(self, request, on_text):
            on_text("half ")
            raise ModelProviderError("HTTP 503 boom")

    skill = AnalyzeSkill(model_router=_BoomRouter())
    got: list[str] = []
    tok = streaming.set_current_sink(got.append)
    try:
        out = skill.execute(_skill_input())
    finally:
        streaming.reset_current_sink(tok)

    assert out.success is False
    assert got == ["half "]  # partial was provisional; the result is a failure


# ── Task 7: Core binds the ambient sink; cancellation stays deterministic ──


class _StreamingHandler:
    """Stand-in for the analyze path: pushes deltas through the ambient sink."""

    def run(self, task_input, context) -> TaskOutput:
        streaming.emit_token("Hel")
        streaming.emit_token("lo")
        return TaskOutput(content="Hello", success=True)


def _streaming_brain() -> Brain:
    plan = ExecutionPlan(steps=(PlanStep(description="Explain the findings", intent="explain"),))
    return Brain(
        planner=StaticPlanner([plan]),
        executor=TaskExecutor(handlers={"explain": _StreamingHandler()}),
    )


def test_brain_binds_sink_for_the_duration_of_a_run():
    captured: list[str] = []
    res = _streaming_brain().run(
        "Explain the findings", stream_sink=captured.append,
    )
    assert res.status == ResultStatus.COMPLETED
    assert captured == ["Hel", "lo"]
    assert res.output == "Hello"  # authoritative result text, not the delta tail


def test_brain_resets_sink_after_the_run():
    _streaming_brain().run("Explain the findings", stream_sink=lambda t: None)
    assert streaming.current_sink() is None


def test_brain_without_sink_behaves_exactly_as_before():
    res = _streaming_brain().run("Explain the findings")
    assert res.status == ResultStatus.COMPLETED
    assert streaming.current_sink() is None


def test_cancellation_during_stream_is_cancelled_not_completed():
    token = CancellationToken()
    token.cancel()
    res = _streaming_brain().run(
        "Explain the findings", cancel_token=token, stream_sink=lambda t: None,
    )
    assert res.status == ResultStatus.CANCELLED
    assert res.status != ResultStatus.COMPLETED


def test_fast_path_still_works_with_a_bound_sink():
    captured: list[str] = []
    res = _streaming_brain().run("what is 15 * 28?", stream_sink=captured.append)
    assert res.status == ResultStatus.COMPLETED
