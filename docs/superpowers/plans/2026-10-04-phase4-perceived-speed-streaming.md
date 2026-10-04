# Phase 4 — Perceived Speed (Response Streaming) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Mamba feel responsive by streaming model output incrementally to the UI, while keeping the existing complete-response path fully valid.

**Architecture:** Streaming is an **optional presentation/transport layer**, not a new execution system. A dependency-free ambient "stream sink" callback (modeled exactly on the existing `core/cancellation.py` ContextVar) is bound by `Brain.run` on the worker thread; the single answer-producing model call in `AnalyzeSkill` pushes text deltas into it. Providers expose an optional `stream()` selected by capability detection; the router preserves Phase 3's fallback rules but adds a hard "no provider switch after the first token" gate. Transport emits structured `delta`/`progress`/`complete`/`error`/`cancelled` events over the **existing `/live` WebSocket only** (the path the UI actually uses, `src/audio.ts:201`); the React UI appends deltas in place as **provisional** output and reconciles/finalizes from the authoritative `ExecutionResult`-derived frames. `/api/chat` stays the non-streaming JSON endpoint it is today.

**Tech Stack:** Python 3.12.9; FastAPI 0.115.6 + Starlette `StreamingResponse` (no new deps); `urllib.request` SSE parsing for OpenAI-compatible providers; `google-genai` SDK `generate_content_stream` for Gemini; stdlib `contextvars`. Frontend: React 18 + TypeScript (strict), Vite, `motion` — **no markdown library, no frontend test runner**. Test runner: `.venv/Scripts/python.exe -m pytest` (pytest 9.1.1, no pytest-asyncio; async via `asyncio.run` + FastAPI `TestClient`).

**Spec:** `C:\Users\Ḥ\.qoder\tmp\C--mamba\attachments\74c3fcd0-a0e3-4abc-9a7b-47e7b918cdee\a632b166-7bb7-40c0-bcef-de27b4a5c202.txt` (PHASE 4 — PERCEIVED SPEED). This plan argues from that spec; executors read both.

## Global Constraints

These are copied from the spec and apply to **every** task below.

- **Do NOT redesign Mamba.** Do NOT create a second execution system, a second runtime, a new streaming server, a separate AI backend, or a new orchestration layer (Parts 4, MASTER ARCHITECTURE, SCOPE).
- **The existing complete-response path MUST remain valid.** Streaming is optional; if a provider cannot stream, `request → provider.invoke() → complete response → existing UI completion` still works (Part 2, IMPORTANT DESIGN RULE).
- **Provider independence preserved; no provider-specific logic in the UI or in Core.** No hardcoded Gemini/Groq/NVIDIA behavior in `core/` (Part 2). Use capability detection (`ModelInfo.capabilities["streaming"]`).
- **Streaming must never bypass:** permissions, approval, target binding, verification, cancellation, execution state (Part 14). The planner and the frontend are **non-authoritative**; the frontend cannot declare approved/verified/successful/completed (Part 14).
- **Phase 1 security, Phase 2 cancellation, and Phase 3 routing/timing must keep passing.** Do not modify Phase 3 optimization strategy except a strictly-required compatibility fix (Phase 4 Goal, Parts 8, 10, 14, 16).
- **Cancellation is deterministic.** A cancelled stream yields `CANCELLED` and must NOT become SUCCESS/VERIFIED/COMPLETED; cancellation must never trigger provider fallback (Parts 8, 10).
- **Partial output is never persisted as completed memory.** Memory capture happens only after successful completion (Parts 3, 9, 13).
- **Provider fallback during streaming:** failure **before** any token → bounded Phase 3 fallback allowed; failure **after** meaningful output → deterministic failure, never silently switch providers (Part 10).
- **Do not leak** credentials, API keys, stack traces, private memory, or raw browser auth in streamed/chunk output (Part 7). Do not fabricate "streaming" with placeholder text (Part 6).
- **Keep progress (stage) and streaming (tokens) as separate concepts** in both events and UI (Part 6). Avoid excessive events/messages; batch/throttle tiny chunks (Parts 12, 15).
- **Voice is out of scope.** Existing STT/TTS/continuous-conversation behavior must not regress; do not stream text into TTS (Part 11). Phase 5 owns voice.
- **Do not change the visual identity, the Orb, or do a CSS/UI redesign** (Part 5, SCOPE).
- **Git:** the implementer runs **no** git write commands. The only allowed git command is the read-only `git rev-parse --show-toplevel` (must equal `C:/mamba`) before modifying. The user performs all commits/checkpoints. Each "checkpoint" step below means: *pause and let the user commit; do not run git yourself* (Spec §Git Rules).
- **Save location** for any new docs: this `docs/superpowers/plans/` directory (user preference for plans).

## Approved Adjustments (owner review, 2026-10-04 — these override the text below where they differ)

1. **SSE is NOT mandatory — `/live` first.** Implement `/live` WebSocket streaming (the existing Mamba UI path). `grep` confirmed `/api/chat` has **no** streaming consumer (only `tests/test_api_server.py`, `tests/test_phase2_runtime_reliability.py`, and docs), so **Task 8 Step 4 (SSE `/api/chat`) is dropped** and `/api/chat` remains byte-identical JSON. Phase 4 is not expanded to add another transport. Spec Part 4 is satisfied by its own escape clause: "If the existing architecture has a better established mechanism, preserve it."
2. **Streamed text is provisional.** Deltas are incremental UI output only; `ExecutionResult` remains the authoritative final result. The final `complete`/`turnComplete` frames must **reconcile** the UI — replace accumulated delta text with the authoritative content, and never let the last delta be treated as proof of successful execution.
3. **Streaming stays scoped to the `AnalyzeSkill` answer-generation path.** No "streamable skill" framework, no skill-architecture redesign, no other skill gains a `stream()`.

---

## File Structure

Responsibilities (each file has one clear job). Files marked (new) are created; the rest are modified.

**Model layer (streaming abstraction):**
- `core/streaming.py` (new) — the ambient stream-sink ContextVar + `set_current_sink`/`reset_current_sink`/`current_sink`/`emit_token`. Dependency-free leaf, mirroring `core/cancellation.py`.
- `models/provider.py` — add a concrete default `stream()` to `BaseModelProvider` (raises unless a provider overrides it).
- `models/protocols.py` — document the optional `stream` capability on `ModelProvider` (structural, duck-typed like `invoke`).
- `models/providers/_openai_sse.py` (new) — shared SSE line parser + chunk extractor for OpenAI-compatible `/chat/completions` streaming (used by Groq + NVIDIA).
- `models/providers/groq.py`, `nvidia.py` — implement real `stream()` via `_openai_sse`; set `capabilities["streaming"]=True`.
- `models/providers/gemini.py` — implement `stream()` via `generate_content_stream`; set `capabilities["streaming"]` from SDK availability.
- `models/router.py` — add `stream(request, on_text)` with the first-token fallback gate; add `_provider_supports_streaming`.

**Core attach (answer generation + binding):**
- `skills/analyze.py` — prefer `router.stream(request, sink)` when an ambient sink is bound and the path supports streaming; else unchanged `router.invoke`.
- `core/brain.py` — `run(..., stream_sink=None)` binds the ambient sink on the worker; streaming chunks respect the ambient cancellation token; final memory/observation path unchanged.
- `core/runtime.py` — `run(..., stream_sink=None)` passes through to `Brain.run` (kwargs pattern already present).

**Transport:**
- `api/server.py` — extend `/live` to emit `delta` frames (coalesced) + map milestones to `{"type":"progress","stage":...}` + `complete`/`error`/`cancelled`; **`/api/chat` unchanged** (adjustment 1). Voice branch frame sequence must stay byte-compatible with `tests/test_api_server.py`.

**Frontend (incremental rendering, presentation only):**
- `src/audio.ts` — handle `delta` frames; expose `onDelta`/`onTurnComplete` (fixed to fire for text turns); add `sendCancel()`.
- `src/MambaApp.tsx` — streaming message model: append-or-patch a single in-flight assistant entry by turn id; finalize on `transcription(role=model)`/`turnComplete`; clear streaming on `error`/cancel; de-dupe the user echo; stable ids.
- `src/TranscriptPanel.tsx` — render growing text as a plain text node (no per-chunk `dangerouslySetInnerHTML` teardown); follow-tail autoscroll; memoized row; optional `.is-streaming` caret class.
- `src/Composer.tsx` — stop/cancel affordance while thinking.
- `src/index.css` — `.t-body.is-streaming` caret hook only (no redesign).

**Tests & docs:**
- `tests/test_phase4_streaming.py` (new) — model/streaming unit tests (providers via injected fake seams, router, analyze, brain binding).
- `tests/test_phase4_transport.py` (new) — `/live` delta + lifecycle frames + authoritative reconciliation.
- `docs/ARCHITECTURE.md` — new §23.4 "Streaming (Phase 4)" note.

---

## Dependency order

`Task 1 → 2 → {3,4,5} → 6 → 7 → 8 → 9 → 10 → 11`. Tasks 3/4/5 are provider+router work that each depend only on 1–2 and are individually testable. Task 6 needs the router `stream` (5). Task 7 needs the ambient sink (1) and analyze attach (6). Task 8 needs the sink bound in runtime/brain (7). Task 9 (UI) needs the transport events (8). Task 10 is the gated real-provider run + full regression. Task 11 is docs.

---

## Task 1: Ambient stream-sink (`core/streaming.py`)

**Files:**
- Create: `core/streaming.py`
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Consumes: nothing (dependency-free leaf; stdlib `contextvars` only).
- Produces:
  - `core.streaming.set_current_sink(sink: Callable[[str], None] | None) -> contextvars.Token`
  - `core.streaming.reset_current_sink(token) -> None`
  - `core.streaming.current_sink() -> Callable[[str], None] | None`
  - `core.streaming.emit_token(text: str) -> bool` — calls the current sink with `text` if one is bound; returns whether a sink received it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase4_streaming.py`:

```python
"""Phase 4 streaming tests (sink, providers, router, analyze, brain, transport)."""
from __future__ import annotations

import core.streaming as streaming


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
    # default context has no sink
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.streaming'`.

- [ ] **Step 3: Write the minimal implementation**

Create `core/streaming.py`:

```python
"""Ambient model-response stream sink.

Mirrors the cooperative-cancellation design in :mod:`core.cancellation`:
``Brain.run`` binds a sink for the duration of one execution on its single
worker thread, so deep collaborators that cannot receive it via parameters
— notably ``AnalyzeSkill``'s answer model call — can push incremental text
without invasive signature changes.

A sink is any ``Callable[[str], None]`` receiving ordered text deltas. It is
presentation-only: it never replaces the authoritative complete ``ModelResponse``
that flows into observations, verification, memory, and the final result.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from collections.abc import Callable
from typing import Any

_current_sink: ContextVar[Callable[[str], None] | None] = ContextVar(
    "mamba_stream_sink", default=None
)


def set_current_sink(sink: Callable[[str], None] | None) -> Token[Any]:
    """Bind ``sink`` as the ambient stream sink for this context."""
    return _current_sink.set(sink)


def reset_current_token(reset_token: Token[Any]) -> None:  # noqa: D401 - symmetry alias
    reset_current_sink(reset_token)


def reset_current_sink(reset_token: Token[Any]) -> None:
    """Restore the previous sink. Best-effort across contexts."""
    try:
        _current_sink.reset(reset_token)
    except (ValueError, LookupError):
        _current_sink.set(None)


def current_sink() -> Callable[[str], None] | None:
    return _current_sink.get()


def emit_token(text: str) -> bool:
    """Push ``text`` to the bound sink. Returns True if a sink received it."""
    sink = _current_sink.get()
    if sink is None:
        return False
    sink(text)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint**

Pause; let the user commit. Do **not** run git yourself.

---

## Task 2: Provider streaming contract + capability detection

Adds the optional `stream()` surface and the streaming capability flag, **without** forcing any concrete provider yet. The default `stream()` raises; a provider that can stream will override it and advertise `capabilities["streaming"] = True`.

**Files:**
- Modify: `models/provider.py:10-22`
- Modify: `models/protocols.py:10-22`
- Modify: `models/router.py` (add `_provider_supports_streaming` helper near `_supports_multimodal` at `router.py:97-99`)
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Produces:
  - `BaseModelProvider.stream(self, request: ModelRequest, on_text: Callable[[str], None]) -> ModelResponse` — default raises `ModelProviderError("<provider> does not support streaming")`.
  - `models.router._provider_supports_streaming(info: ModelInfo) -> bool` — truthiness of `info.capabilities.get("streaming")`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase4_streaming.py`:

```python
import pytest

from models.errors import ModelProviderError
from models.provider import BaseModelProvider
from models.router import _provider_supports_streaming
from models.types import ModelInfo, ModelRequest, ModelResponse


class _InvokeOnly(BaseModelProvider):
    def invoke(self, request):
        return ModelResponse(content="ok", provider="x", model="m")


def test_default_stream_raises():
    p = _InvokeOnly(ModelInfo(provider="x", model="m"))
    with pytest.raises(ModelProviderError):
        p.stream(ModelRequest(input="hi"), lambda t: None)


def test_capability_detection_is_truthiness_not_membership():
    assert _provider_supports_streaming(ModelInfo(provider="x", model="m", capabilities={"streaming": True})) is True
    # {"streaming": False} must NOT be treated as capable (membership bug class)
    assert _provider_supports_streaming(ModelInfo(provider="x", model="m", capabilities={"streaming": False})) is False
    assert _provider_supports_streaming(ModelInfo(provider="x", model="m", capabilities={})) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -q`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'stream'` / `_provider_supports_streaming` not defined.

- [ ] **Step 3: Write the minimal implementation**

In `models/provider.py`, add the import for the callable + error and the default method:

```python
from collections.abc import Callable
from .errors import ModelProviderError  # add to existing import group
from .types import ModelInfo, ModelRequest, ModelResponse


class BaseModelProvider(ABC):
    # ... existing __init__ / info / invoke unchanged ...

    def stream(self, request: ModelRequest, on_text: Callable[[str], None]) -> ModelResponse:
        """Stream a response, pushing ordered text deltas to ``on_text``.

        Providers that cannot stream do NOT implement this and MUST NOT set
        ``ModelInfo.capabilities["streaming"]``; callers fall back to ``invoke()``.
        A streaming provider MUST return the complete ``ModelResponse`` in addition
        to the deltas, so the authoritative path is unchanged.
        """
        raise ModelProviderError(
            f"{self._info.provider} provider does not support streaming"
        )
```

In `models/protocols.py`, document the optional surface (structural; callers still duck-type with `hasattr(..., "stream")` so existing fakes keep working):

```python
class ModelProvider(Protocol):
    @property
    def info(self) -> ModelInfo: ...
    def invoke(self, request: ModelRequest) -> ModelResponse: ...
    # Optional: providers advertising ModelInfo.capabilities["streaming"]=True
    # also implement ``stream(request, on_text) -> ModelResponse``.
```

In `models/router.py`, add next to `_supports_multimodal` (`router.py:97-99`):

```python
def _provider_supports_streaming(info: ModelInfo) -> bool:
    return bool(info.capabilities.get("streaming"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py tests/test_model_router.py -q`
Expected: PASS (new tests + all existing router tests).

- [ ] **Step 5: Checkpoint**

Pause; user commits.

---

## Task 3: Gemini provider streaming

Gemini already uses the `google-genai` SDK (`gemini.py:78,121`), which ships `models.generate_content_stream` (sync). This is the lowest-risk real streaming provider.

**Files:**
- Modify: `models/providers/gemini.py` (`invoke` near `:121-135`; capability dict `:94-106`)
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Consumes: `self._client.models.generate_content_stream(model, prompt, config=...) -> iterator of objects with .text` (SDK). `on_text` callback.
- Produces: `GeminiModelProvider.stream(request, on_text) -> ModelResponse` — identical final `ModelResponse` shape as `invoke` (`content/provider/model/success/metadata`); sets `capabilities["streaming"]=True`.

- [ ] **Step 1: Write the failing test**

```python
from models.providers.gemini import GeminiModelProvider


class _Chunk:
    def __init__(self, text): self.text = text


class _FakeGeminiModels:
    def __init__(self, chunks): self._chunks = chunks; self._streamed = False
    def generate_content(self, **k):  # non-stream path used by invoke()
        return _Chunk("".join(c.text for c in self._chunks))
    def generate_content_stream(self, model, prompt, config=None):
        self._streamed = True
        return iter(self._chunks)


class _FakeGeminiClient:
    def __init__(self, chunks): self.models = _FakeGeminiModels(chunks)


def test_gemini_stream_emits_ordered_chunks_and_returns_full_response():
    chunks = [_Chunk("Hel"), _Chunk("lo "), _Chunk("there")]
    p = GeminiModelProvider(api_key="k", _client=_FakeGeminiClient(chunks))
    assert p.info.capabilities.get("streaming") is True
    seen: list[str] = []
    resp = p.stream(ModelRequest(input="hi"), seen.append)
    assert seen == ["Hel", "lo ", "there"]
    assert resp.success is True
    assert resp.content == "Hello there"   # complete, authoritative
    assert resp.provider == "gemini"


def test_gemini_stream_failure_midway_returns_partial_as_failure():
    class _Boom(_Chunk):
        def __init__(self, text):
            super().__init__(text)
    # model raises on the SECOND yielded chunk
    class _BoomModels(_FakeGeminiModels):
        def generate_content_stream(self, model, prompt, config=None):
            def gen():
                yield _Chunk("part")
                raise RuntimeError("HTTP 503 upstream boom")
            return gen()
    client = _FakeGeminiClient([]); client.models = _BoomModels([])
    p = GeminiModelProvider(api_key="k", _client=client)
    got: list[str] = []
    with pytest.raises(ModelProviderError):
        p.stream(ModelRequest(input="hi"), got.append)
    assert got == ["part"]   # partial emitted, then surfaced as error (never masked)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k gemini -q`
Expected: FAIL — provider `capabilities` has no `streaming`, and `stream` inherited default raises.

- [ ] **Step 3: Write the implementation**

In `gemini.py`, set the capability when the SDK surface is present, next to the existing capability build (`gemini.py:94-106`):

```python
capabilities["streaming"] = bool(getattr(self._client.models, "generate_content_stream", None))
```

Add `stream()` reusing `invoke`'s prompt/config construction (mirror the kwargs `invoke` already builds at `gemini.py:121-135`):

```python
def stream(self, request: ModelRequest, on_text) -> ModelResponse:
    """Stream Gemini output, pushing text deltas to ``on_text``."""
    if not _provider_supports_streaming(self._info):
        return self.invoke(request)          # degrade honestly to the complete path
    model_to_use = request.model_id or self._model
    kwargs = self._build_generate_kwargs(request, model=model_to_use)  # reuse invoke's builder
    pieces: list[str] = []
    try:
        for chunk in self._client.models.generate_content_stream(**kwargs):
            text = (getattr(chunk, "text", "") or "")
            if text:
                pieces.append(text)
                on_text(text)
    except Exception as exc:
        # Preserve the flattened "HTTP <code>" substring so router classification works.
        raise ModelProviderError(self._sanitize(f"Gemini stream failed: {exc}")) from exc
    content = "".join(pieces)
    if not content:
        raise ModelProviderError("Gemini stream returned empty content")
    return ModelResponse(content=content, provider="gemini", model=model_to_use,
                         success=True, metadata={"streamed": True})
```

If `invoke` currently builds its SDK call inline (not in a `_build_generate_kwargs` helper), extract that inline block into `_build_generate_kwargs(request, *, model) -> dict` and have BOTH `invoke` and `stream` use it — a behavior-preserving refactor; verify `invoke` still returns the same `ModelResponse`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k gemini -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint**

Pause; user commits.

---

## Task 4: Groq + NVIDIA streaming via shared SSE parser

Both use `urllib.request` against OpenAI-compatible `/chat/completions` (`groq.py:93-102`, `nvidia.py:105-113`). Streaming adds `"stream": true` to the body and parses `text/event-stream` SSE lines (`data: {...}\n\n`), reading `choices[0].delta.content`.

**Files:**
- Create: `models/providers/_openai_sse.py`
- Modify: `models/providers/groq.py` (capability `:71-78`; body `:156-177`; new `stream()` + `_http_stream` seam)
- Modify: `models/providers/nvidia.py` (capability `:78-91`; body `:197-218`; new `stream()` + `_http_stream` seam)
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Produces: `models.providers._openai_sse.parse_sse_delta(line: str) -> str | None` — returns the text delta for one `data:` SSE line, `"__DONE__"` sentinel for `[DONE]`, else `None` (ignore blank/`:` comment lines).
- Produces on each provider: `stream(request, on_text) -> ModelResponse`; `capabilities["streaming"]=True`; injectable `_http_stream(url, headers, body, timeout) -> iterable[str]` seam (yields SSE lines) for tests without network.

- [ ] **Step 1: Write the failing tests**

```python
from models.providers._openai_sse import parse_sse_delta


def test_parse_sse_delta_extracts_content():
    assert parse_sse_delta('data: {"choices":[{"delta":{"content":"Hi"}}]}') == "Hi"
    assert parse_sse_delta('data: {"choices":[{"delta":{}}]}') is None       # role-only line
    assert parse_sse_delta("") is None                                        # keep-alive
    assert parse_sse_delta(": ping") is None                                  # comment
    assert parse_sse_delta("data: [DONE]") == "__DONE__"


def _fake_sse_http(lines):
    def _http_stream(*, url, headers, body, timeout):
        assert body.get("stream") is True
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
    import urllib.error
    from models.providers.groq import GroqModelProvider
    def _boom(*, url, headers, body, timeout):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
    p = GroqModelProvider(api_key="k", _http_stream=_boom)
    with pytest.raises(ModelProviderError) as ei:
        p.stream(ModelRequest(input="hi"), lambda t: None)
    assert "429" in str(ei.value)   # router._classify_transient depends on this
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k "sse or groq" -q`
Expected: FAIL — `ModuleNotFoundError: models.providers._openai_sse`, and `GroqModelProvider.__init__()` got unexpected keyword `_http_stream`.

- [ ] **Step 3: Write the implementation**

Create `models/providers/_openai_sse.py`:

```python
"""Shared SSE (text/event-stream) delta parsing for OpenAI-compatible providers."""
from __future__ import annotations

import json

DONE = "__DONE__"


def parse_sse_delta(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith(":"):
        return None
    if not s.startswith("data:"):
        return None
    payload = s[len("data:"):].strip()
    if payload == "[DONE]":
        return DONE
    try:
        obj = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return None
    choices = obj.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content or None
```

In `groq.py`: add `capabilities["streaming"] = True` to the dict at `groq.py:71-78`; add `_http_stream=None` kwarg stored as `self._http_stream = _http_stream or self._default_http_stream` (mirror the existing `_http_post` seam at `:69`); add `"stream": True` handling in `_build_body` (accept a `stream: bool = False` arg and set `body["stream"] = True`); implement:

```python
def stream(self, request, on_text):
    if request.has_images:
        raise ModelProviderError("Groq provider does not support multimodal/image input")
    model_to_use = request.model_id or self._model
    messages = self._build_messages(request)
    body = self._build_body(model_to_use, messages, request.parameters, stream=True)
    pieces: list[str] = []
    lines = self._http_stream(
        url=f"{self._base_url}/chat/completions",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "Authorization": f"Bearer {self._api_key}", "User-Agent": "Mamba-AI/1.0"},
        body=body, timeout=self._timeout,
    )
    for line in lines:
        delta = parse_sse_delta(line)
        if delta == DONE:
            break
        if delta:
            pieces.append(delta)
            on_text(delta)
    content = "".join(pieces)
    if not content:
        raise ModelProviderError("Groq stream returned empty content")
    return ModelResponse(content=content, provider="groq", model=model_to_use,
                         success=True, metadata={"streamed": True})
```

Add `_default_http_stream` that opens `urllib.request.urlopen(req, timeout=...)` and yields decoded lines (`for raw in resp: yield raw.decode("utf-8", "replace")`), wrapping `HTTPError`/`URLError`/`TimeoutError` into `ModelProviderError` with the same `"Groq API HTTP {exc.code}: ..."` message shape as `_default_http_post` (`groq.py:254-278`) so the `"HTTP <code>"` substring is preserved. Import `parse_sse_delta` from `._openai_sse`.

Apply the identical changes to `nvidia.py` (`:78-91` capability, `:57,76` seam pattern, `:197-218` body) with `provider="nvidia"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py tests/test_model_router.py -q`
Expected: PASS.

- [ ] **Step 5: Verify providers' non-stream path is untouched**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q -k "gemini or groq or nvidia or provider"`
Expected: PASS (no regression to `invoke`).

- [ ] **Step 6: Checkpoint**

Pause; user commits.

---

## Task 5: Router streaming with the first-token fallback gate

**Files:**
- Modify: `models/router.py:237-291` (add `stream` beside `invoke`; reuse `_classify_transient`, `route_candidates`, `_TOTAL_TIMEOUT`, `deadline`)
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Consumes: `provider.stream(request, on_text)` / `provider.invoke(request)`; `core.cancellation.MambaCancelledError`; `_provider_supports_streaming`; `_classify_transient` (Phase 3).
- Produces: `DefaultModelRouter.stream(self, request: ModelRequest, on_text: Callable[[str], None]) -> ModelResponse`.

**Fallback gate (Part 10, the load-bearing rule):**
- Provider selection stays Phase 3 order. For each candidate: if it supports streaming, `provider.stream(request, tracked_cb)`; else `resp = provider.invoke(request); on_text(resp.content); return resp` (complete-path degrade — still valid, honest).
- `tracked_cb` sets `emitted=True` before forwarding to the caller's `on_text`.
- On failure: re-raise `MambaCancelledError` immediately (never fallback on cancel). If `emitted` is already `True` → re-raise the original error (NEVER switch providers mid-stream). If `not emitted` → apply Phase 3 rules: permanent → raise; transient → continue to next candidate; honor the `deadline`.
- On success return the provider's complete `ModelResponse`.

- [ ] **Step 1: Write the failing tests**

Reuse `FakeProvider` from `tests.test_model_router`. Add a streaming-capable fake.

```python
from models.router import DefaultModelRouter
from tests.test_model_router import FakeProvider


class _StreamProvider(FakeProvider):
    """FakeProvider that streams given chunks; optionally raises after N chunks."""
    def __init__(self, provider, model, chunks, raise_after=None, exc=None):
        super().__init__(provider, model, capabilities={"chat": True, "streaming": True})
        self._chunks = chunks; self._raise_after = raise_after; self._exc = exc
    def stream(self, request, on_text):
        for i, c in enumerate(self._chunks):
            if self._raise_after is not None and i >= self._raise_after:
                raise self._exc
            on_text(c)
        from models.types import ModelResponse
        return ModelResponse(content="".join(self._chunks), provider=self._info.provider,
                             model=self._info.model)


def _req(): return ModelRequest(input="hi")


def test_stream_emits_all_chunks_then_complete():
    got: list[str] = []
    r = DefaultModelRouter([_StreamProvider("groq", "m", ["A", "B", "C"])])
    resp = r.stream(_req(), got.append)
    assert got == ["A", "B", "C"] and resp.content == "ABC"


def test_failure_before_first_token_falls_back_bounded():
    got: list[str] = []
    bad = _StreamProvider("a", "m", [], raise_after=0, exc=RuntimeError("HTTP 503 boom"))
    good = _StreamProvider("b", "m", ["X"])
    r = DefaultModelRouter([bad, good])
    resp = r.stream(_req(), got.append)
    assert resp.provider == "b" and got == ["X"]


def test_failure_after_first_token_does_not_switch_providers():
    got: list[str] = []
    bad = _StreamProvider("a", "m", ["par"], raise_after=1, exc=RuntimeError("HTTP 503 boom"))
    never = _StreamProvider("b", "m", ["SHOULD_NOT_APPEAR"])
    r = DefaultModelRouter([bad, never])
    with pytest.raises(ModelProviderError):
        r.stream(_req(), got.append)
    assert got == ["par"]                       # partial shown, then deterministic failure
    assert "SHOULD_NOT_APPEAR" not in got       # provider B never started


def test_cancellation_never_triggers_stream_fallback():
    from core.cancellation import MambaCancelledError
    cancelled = _StreamProvider("a", "m", ["x"], raise_after=1, exc=MambaCancelledError())
    backup = _StreamProvider("b", "m", ["y"])
    r = DefaultModelRouter([cancelled, backup])
    with pytest.raises(MambaCancelledError):
        r.stream(_req(), lambda t: None)


def test_non_streaming_provider_degrades_to_complete():
    got: list[str] = []
    r = DefaultModelRouter([FakeProvider("groq", "m", capabilities={"chat": True})])  # no streaming
    resp = r.stream(_req(), got.append)
    assert resp.success and got == [resp.content]   # whole content delivered once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k "stream_emits or falls_back or switch or cancel or degrade" -q`
Expected: FAIL — `AttributeError: 'DefaultModelRouter' object has no attribute 'stream'`.

- [ ] **Step 3: Write the implementation**

Add `stream` after `invoke` (`router.py:291`). Import `MambaCancelledError` lazily (as `invoke` does at `router.py:247`), and `_provider_supports_streaming` is local:

```python
def stream(self, request, on_text):
    """Stream from the first viable candidate, honoring the Part 10 first-token gate."""
    from core.cancellation import MambaCancelledError

    candidates = self.route_candidates(request)
    last_error: Exception | None = None
    attempts = 0
    deadline = time.monotonic() + _TOTAL_TIMEOUT if len(candidates) > 1 else None

    for provider in candidates:
        attempts += 1
        emitted = False

        def _tracked(delta: str, _cb=on_text) -> None:
            nonlocal emitted
            emitted = True
            _cb(delta)

        try:
            if _provider_supports_streaming(self._provider_info(provider)) and hasattr(provider, "stream"):
                return provider.stream(request, _tracked)
            response = provider.invoke(request)          # complete-path degrade
            if response.success:
                on_text(response.content)
                return response
            last_error = None
        except MambaCancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if emitted:
                raise                                 # PART 10: never switch mid-stream
            if not _classify_transient(exc):
                raise                                 # permanent: stop immediately
            if deadline is not None and time.monotonic() >= deadline:
                break                                 # bounded fallback
            continue

    if last_error is not None:
        raise last_error
    raise ModelRoutingError("all candidate model providers failed streaming")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py tests/test_model_router.py tests/test_phase3_latency.py -q`
Expected: PASS (Phase 3 fallback tests still green — `invoke` untouched).

- [ ] **Step 5: Checkpoint**

Pause; user commits.

---

## Task 6: Analyze-skill streaming attach

The answer a user sees is produced at `skills/analyze.py:240` (`self._router.invoke(request)`). Attach streaming there, gated by an ambient sink.

**Files:**
- Modify: `skills/analyze.py:231-278`
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Consumes: `core.streaming.current_sink()`; `router.stream(request, on_text)`; `router.invoke(request)`.
- Produces: unchanged `SkillOutput(content=<full text>, success=True, metadata={...})`. Streaming never changes what flows into observations/memory.

- [ ] **Step 1: Write the failing test**

```python
import core.streaming as streaming
from core.context import ExecutionContext
from core.types import UserRequest
from skills.analyze import AnalyzeSkill
from skills.types import SkillInput
from tasks.types import TaskInput, TaskOutput
from models.types import ModelResponse


class _StreamRouter:
    def __init__(self): self.stream_calls = 0; self.invoke_calls = 0
    def invoke(self, request):
        self.invoke_calls += 1
        return ModelResponse(content="full answer", provider="groq", model="m")
    def stream(self, request, on_text):
        self.stream_calls += 1
        for d in ["full ", "answer"]:
            on_text(d)
        return ModelResponse(content="full answer", provider="groq", model="m")


def _input(goal="explain my project"):
    ur = UserRequest(goal=goal)
    ctx = ExecutionContext.from_request(ur)
    ti = TaskInput(step_id="s1", description=goal, intent="explain",
                  execution_id=ctx.execution_id, goal=goal, step_metadata={})
    return SkillInput.from_task(ti, ctx)


def test_analyze_uses_stream_when_sink_bound():
    router = _StreamRouter()
    skill = AnalyzeSkill(model_router=router)
    got: list[str] = []
    tok = streaming.set_current_sink(got.append)
    try:
        out = skill.execute(_input())
    finally:
        streaming.reset_current_sink(tok)
    assert out.success and out.content == "full answer"
    assert router.stream_calls == 1 and router.invoke_calls == 0
    assert got == ["full ", "answer"]


def test_analyze_uses_invoke_when_no_sink():
    router = _StreamRouter()
    skill = AnalyzeSkill(model_router=router)
    out = skill.execute(_input())
    assert out.success and out.content == "full answer"
    assert router.invoke_calls == 1 and router.stream_calls == 0
```

`TaskInput` requires `step_id`, `description`, `intent`, `execution_id`, `goal`, and optionally `step_metadata` (verified at `tasks/types.py:12-32`); `SkillInput.from_task(task_input, context)` builds the skill input (verified at `skills/types.py:19-21`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k "analyze" -q`
Expected: FAIL — with a sink bound, `stream_calls == 0` (currently always `invoke`).

- [ ] **Step 3: Write the implementation**

In `skills/analyze.py`, import the sink reader (`from core.streaming import current_sink`) and change the invocation block (`:238-243`) to:

```python
        sink = current_sink()
        try:
            if sink is not None and hasattr(self._router, "stream"):
                response = self._router.stream(request, sink)
            elif hasattr(self._router, "invoke"):
                response = self._router.invoke(request)
            else:
                provider = self._router.route(request)
                response = provider.invoke(request)
        except Exception as exc:
            return SkillOutput(
                content=f"Analysis model invocation failed: {exc}",
                success=False,
                metadata={"error": type(exc).__name__},
            )
```

Leave everything after (the `not response.success` check, `content = (response.content or "").strip()`, the `SkillOutput(content=...)` return at `:274-278`) **unchanged** — memory and observations consume only the complete `response.content`. The three non-model early returns (`:187-229`) already produce zero deltas, which is correct.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k "analyze" tests/ -q -k "analyze or skill"`
Expected: PASS.

- [ ] **Step 5: Checkpoint**

Pause; user commits.

---

## Task 7: Bind the sink in `Brain.run` / `MambaRuntime.run` + cancellation

**Files:**
- Modify: `core/brain.py:554-578` (`run` signature + sink bind)
- Modify: `core/runtime.py:34-52` (pass-through kwarg)
- Test: `tests/test_phase4_streaming.py`

**Interfaces:**
- Produces: `Brain.run(request, *, on_progress=None, cancel_token=None, stream_sink=None) -> ExecutionResult`; `MambaRuntime.run(request, *, on_progress=None, cancel_token=None, stream_sink=None)`.
- Behavior: when `stream_sink` is callable, bind it as the ambient sink for the duration of the run (on the worker thread, exactly like `set_current_token` at `brain.py:573`). Cancellation during streaming already routes through the ambient token → `MambaCancelledError` → `_cancel_result` → `CANCELLED` (`brain.py:776-777, 784-801`); provider `stream()` checks `raise_if_cancelled()` at the chunk boundary (added in Tasks 3–4 via the ambient token). No new progress system: milestones remain on `on_progress`.

- [ ] **Step 1: Write the failing test**

```python
from core.brain import Brain
from core.cancellation import CancellationToken, MambaCancelledError
from core.types import ExecutionPlan, PlanStep, ResultStatus
from tasks.executor import TaskExecutor
from tests.test_core_lifecycle import EchoTaskHandler, StaticPlanner


def test_brain_binds_sink_during_execution(monkeypatch):
    import core.streaming as streaming
    captured: list[str] = []
    # A "planner+executor" that streams via analyze-style sink when present.
    def _side_effect(context, user_request, on_progress=None, seed_plan=None):
        assert streaming.current_sink() is not None      # bound for the whole run
        streaming.emit_token("hi")
        return user_request  # unused; we raise to end early deterministically
    plan = ExecutionPlan(steps=(PlanStep(description="Say hi", intent="greet"),))
    brain = Brain(planner=StaticPlanner([plan]),
                  executor=TaskExecutor(handlers={"greet": EchoTaskHandler()}))
    brain.run("what is 15 * 28?", stream_sink=captured.append)  # fast path still works
    # arithmetic fast path does not call analyze → sink may receive nothing; assert no crash
    assert isinstance(captured, list)


def test_cancellation_during_stream_is_cancelled_not_completed():
    import core.streaming as streaming
    plan = ExecutionPlan(steps=(PlanStep(description="Say hi", intent="greet"),))
    brain = Brain(planner=StaticPlanner([plan]),
                  executor=TaskExecutor(handlers={"greet": EchoTaskHandler()}))
    tok = CancellationToken(); tok.cancel()
    res = brain.run("explain my project", cancel_token=tok, stream_sink=lambda t: None)
    assert res.status == ResultStatus.CANCELLED
    assert res.status != ResultStatus.COMPLETED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py -k "binds_sink or during_stream" -q`
Expected: FAIL — `Brain.run() got an unexpected keyword argument 'stream_sink'`.

- [ ] **Step 3: Write the implementation**

`core/brain.py` — extend the `run` signature (`:554-560`) and bind/reset alongside the token (`:572-578`):

```python
    def run(self, request, *, on_progress=None, cancel_token=None, stream_sink=None) -> ExecutionResult:
        _progress = on_progress if callable(on_progress) else None
        _sink = stream_sink if callable(stream_sink) else None
        _reset = set_current_token(cancel_token) if cancel_token is not None else None
        _sreset = set_current_sink(_sink) if _sink is not None else None
        try:
            return self._run(request, _progress)
        finally:
            if _sreset is not None:
                reset_current_sink(_sreset)
            if _reset is not None:
                reset_current_token(_reset)
```

Add `from .streaming import set_current_sink, reset_current_sink` to `brain.py`'s core imports. Leave `_run`/`_run_impl`/`_execution_loop`/`_execute_step` untouched — the sink is ambient, so no signature churn propagates to the executor/skill boundary.

`core/runtime.py` — mirror the existing kwargs pattern (`:47-51`):

```python
        if stream_sink is not None:
            kwargs["stream_sink"] = stream_sink
```

and add `stream_sink: Any = None` to the `run` signature (`:38-39`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_streaming.py tests/test_core_lifecycle.py tests/test_phase2_runtime_reliability.py -q`
Expected: PASS (Phase 2 cancellation + lifecycle intact).

- [ ] **Step 5: Verify streaming does not bypass permission (Part 14)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "does_not_bypass_permission" -q`
Expected: PASS — the fast-path permission spy still fires; streaming attaches only at answer synthesis, after permission/verification.

- [ ] **Step 6: Checkpoint**

Pause; user commits.

---

## Task 8: Transport — `/live` delta frames (WebSocket only)

One surface, one event vocabulary (Part 4): `delta`, `progress`, `complete` (`transcription`/`turnComplete`), `error`, `cancelled`. `/live` — the existing Mamba UI path — is extended additively. **`/api/chat` is not touched** (approved adjustment 1: no concrete streaming consumer exists; adding SSE would expand Phase 4 into a second transport). Voice-frame byte-compatibility is preserved.

**Files:**
- Modify: `api/server.py:174-407` (`/live`), imports (`:12-29`)
- Test: `tests/test_phase4_transport.py`

**Interfaces:**
- Consumes: `core.runtime.MambaRuntime.run(..., stream_sink=...)`, `on_progress`, `cancel_token`; `_format_execution_response`.
- Produces:
  - `/live` (text turn): `status/connected`, `transcription/user`, `status/thinking`, zero-or-more `{type:delta,text}`, milestones as `{type:progress,stage}` (keep `milestone` key too for back-compat), then the authoritative `transcription/model` + `turnComplete` + `status/listening`; `status/cancelling` + `cancelled` on cancel.
  - `/api/chat`: unchanged `ChatResponse` JSON.

- [ ] **Step 1: Write the failing tests**

```python
import json
import pytest
from fastapi.testclient import TestClient
from core.brain import Brain
from core.runtime import MambaRuntime
from core.types import ExecutionPlan, PlanStep
from tasks.executor import TaskExecutor
from tests.test_core_lifecycle import StaticPlanner
from api.server import create_app


class _StreamingHandler:
    def execute(self, step, context):
        import core.streaming as streaming
        from core.types import Observation
        streaming.emit_token("part1")
        streaming.emit_token("part2")
        return Observation(step_id=step.id, content="part1part2", success=True)


def _app_for(goal, intent):
    plan = ExecutionPlan(steps=(PlanStep(description=goal, intent=intent),))
    brain = Brain(planner=StaticPlanner([plan]),
                  executor=TaskExecutor(handlers={intent: _StreamingHandler()}))
    return create_app(MambaRuntime(brain=brain))


def test_live_delta_frames_arrive_in_order_then_complete():
    client = TestClient(_app_for("explain stuff", "explain"))
    with client.websocket_connect("/live") as ws:
        ws.send_json({"type": "text", "text": "explain stuff"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f.get("type") == "turnComplete":
                break
        deltas = [f["text"] for f in frames if f.get("type") == "delta"]
        assert deltas == ["part1", "part2"]                     # ordered
        model = [f["text"] for f in frames if f.get("type") == "transcription" and f.get("role") == "model"]
        assert model == ["part1part2"]                           # authoritative complete preserved


def test_live_model_frame_is_authoritative_when_deltas_diverge():
    """Adjustment 2: deltas are provisional; the model frame carries the real result."""
    client = TestClient(_app_for("explain stuff", "explain"))
    with client.websocket_connect("/live") as ws:
        ws.send_json({"type": "text", "text": "explain stuff"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f.get("type") == "turnComplete":
                break
        joined = "".join(f["text"] for f in frames if f.get("type") == "delta")
        model = [f["text"] for f in frames
                 if f.get("type") == "transcription" and f.get("role") == "model"]
        # The authoritative frame is what the UI finalizes to, not the delta tail.
        assert model == ["part1part2"]
        assert model[0].startswith(joined)


def test_api_chat_json_shape_is_unchanged():
    """Adjustment 1: /api/chat stays non-streaming JSON (no SSE added)."""
    client = TestClient(_app_for("explain stuff", "explain"))
    resp = client.post("/api/chat", json={"input": "explain stuff"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert set(resp.json()) >= {"execution_id", "status", "output"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_transport.py -q`
Expected: FAIL — no `delta` frames are emitted on `/live`.

- [ ] **Step 3: Implement `/live` delta streaming**

`api/server.py` — build a coalescing, worker-thread-safe sink beside `make_progress` (`:201-218`). Coalescing satisfies Part 15 (avoid flooding one-chunk-per-message). The first delta flushes immediately (perceived speed); later deltas accumulate until `_DELTA_COALESCE_SECONDS` has elapsed; `flush()` drains the tail and returns the send future so the turn handler can await it **before** the authoritative frame, keeping frame order deterministic:

```python
        def make_stream_sink():
            """Worker-safe sink: coalesces tiny deltas into few frames."""
            buf: list[str] = []
            last_flush = [0.0]

            def _send(batch: str):
                try:
                    return asyncio.run_coroutine_threadsafe(
                        websocket.send_json({"type": "delta", "text": batch}), loop,
                    )
                except Exception:
                    return None

            def on_delta(text: str) -> None:
                buf.append(text)
                now = time.monotonic()
                if last_flush[0] == 0.0 or now - last_flush[0] >= _DELTA_COALESCE_SECONDS:
                    flush()

            def flush():
                if not buf:
                    return None
                batch = "".join(buf)
                buf.clear()
                last_flush[0] = time.monotonic()
                return _send(batch)

            on_delta.flush = flush  # type: ignore[attr-defined]
            return on_delta
```

At turn completion, before sending `transcription(role=model)`: `fut = stream_sink.flush()` then `if fut is not None: await asyncio.wrap_future(fut)`. On the error/cancel paths, drop the buffer instead (never emit provisional text as if it finished).

In the **text** branch only (`api/server.py:338-352`), pass the sink into the run call:

```python
                    stream_sink = make_stream_sink()
                    def _text_turn(token, _prompt=prompt_text, _prog=on_progress_sync, _sink=stream_sink):
                        return runtime.run(_prompt, on_progress=_prog, cancel_token=token, stream_sink=_sink)
                    result = await _run_cancellable(_text_turn)
```

Do **not** pass `stream_sink` on the voice branch (`:313-323`) — voice keeps returning the complete `transcription`/`audio`/`turnComplete` sequence, so `tests/test_api_server.py` voice assertions stay byte-compatible (Part 11). Map existing `{"type":"progress","milestone":...}` to also carry `"stage"` (add `"stage": milestone` in the `make_progress` payload) so the frontend progress channel is meaningful without a second event system. Keep `{"type":"error"}`; on cancel path emit `{"type":"cancelled"}` after the existing `{"type":"status","status":"cancelling"}` (`:252-254`) by inspecting `result.status == ResultStatus.CANCELLED` before the model frame and emitting `cancelled` instead of `transcription/model` + `turnComplete` — never mark cancelled as complete.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase4_transport.py tests/test_api_server.py -q`
Expected: PASS (new + all existing API tests, incl. voice byte-compat and `/api/chat` JSON shape).

- [ ] **Step 5: Checkpoint**

Pause; user commits.

---

## Task 9: Frontend incremental rendering (presentation only)

**Files:**
- Modify: `src/audio.ts` (`:127-138` handler fields; `:235-359` frame chain; `:263-268` status whitelist; `:307-311` turnComplete; add `sendCancel`)
- Modify: `src/MambaApp.tsx` (`:14-20` entry shape; `:26` transcript state; `:162-170` onTranscription; wire onDelta/onTurnComplete; `:279-294` submit + user-echo dedupe)
- Modify: `src/TranscriptPanel.tsx` (`:43,57` drop double-copy or memoize; `:81-86` follow-tail autoscroll; `:315-318` streaming text render)
- Modify: `src/Composer.tsx` (`:75-84` stop affordance)
- Modify (optional): `src/index.css` (`.t-body.is-streaming` caret)

**Behavior:** deltas append to ONE in-flight assistant entry (by a turn-scoped id), not a new entry per chunk; the authoritative `transcription(role=model)`/`turnComplete` finalizes and clears the streaming flag; `error`/cancel clears it too (Part 16 #16). Rendering uses a React text node while streaming (no `dangerouslySetInnerHTML` teardown → no flicker/lost selection). Autoscroll follows the growing tail. There is **no markdown library** in this repo; "preserve Markdown rendering" means keep the current `white-space:pre-wrap` plain-text rendering and only re-apply `highlightMatch` on finalize.

- [ ] **Step 1: `audio.ts` — delta + turn-complete + cancel**

Add handler fields to the constructor params (`:127-138`): `onDelta?: (text: string) => void` and `onTurnComplete?: () => void` and `onCancelled?: () => void`. In `ws.onmessage` (`:235-359`) add, next to the `transcription` branch (`:314-316`):

```ts
if (data.type === "delta") { this.onDelta?.(data.text); }   // fall through (do not return)
if (data.type === "cancelled") { this.onCancelled?.(); }
```

Widen the status whitelist (`:263-268`) to forward `"listening"` and `"cancelling"` so `liveState` can leave `"thinking"`. Fire `onTurnComplete` from the `turnComplete` branch (`:307-311`) BEFORE the `voiceTurnActive` guard that currently swallows typed-turn completion. Add `sendCancel()` beside `sendText` (`:151-155`) sending `{"type":"cancel"}`.

- [ ] **Step 2: `MambaApp.tsx` — streaming message model**

Extend the entry shape (`:14-20`) with `streaming?: boolean`. Replace the per-frame append `onTranscription` (`:162-170`) with append-or-patch keyed on a turn-scoped id: deltas update the open model entry's `content`; the final `transcription(role=model)` replaces it with the authoritative text and sets `streaming:false`; `onTurnComplete`/`onCancelled`/`error` clear any open streaming entry's flag. Suppress the double user-echo: skip appending the server `role:"user"` frame when a matching optimistic entry already exists (`:283-289`). Use `requestAnimationFrame`-coalesced `setTranscript` so a burst of deltas collapses into one paint (Part 15).

- [ ] **Step 3: `TranscriptPanel.tsx` — flicker-free render + follow-tail scroll**

For a streaming entry render `{entry.content}` as a text node (optionally with a `.is-streaming` caret via CSS) instead of `dangerouslySetInnerHTML` (`:315-318`); apply `highlightMatch` only to finalized entries. Change autoscroll (`:81-86`) to also trigger when the last entry's `content.length` grows, not only on list length change. Memoize the row (or remove the redundant local `entries` copy at `:43,57`) so per-chunk updates do not re-render the whole list.

- [ ] **Step 4: `Composer.tsx` — stop affordance**

While a turn is in flight (`liveState === "thinking"`), swap the send button for a stop button (`:75-84`) that calls `audioSession.sendCancel()`. Keep styling identical (no redesign).

- [ ] **Step 5: Typecheck + build**

Run: `npx tsc --noEmit` then `npm run build`
Expected: PASS with zero type errors. (`build` already runs `tsc && vite build` per `package.json:7-14`.)

- [ ] **Step 6: Focused manual check (Part 18)**

Start the backend (`python app.py --server`) and UI (`npm run dev`), then verify by hand, recording observations (no test runner exists for the UI):
1. simple response → streams, then finalizes;
2. longer response → grows smoothly, no flicker/lost selection;
3. markdown/newlines → render as today (`pre-wrap`), no broken HTML;
4. streaming completion → caret cleared, message stays intact;
5. streaming cancellation → stop button → `cancelled` state, partial NOT marked complete;
6. provider error mid-stream → partial shown, marked failed (not silent success);
7. existing voice flow → unchanged (one `audio` blob + final text, continuous conversation still works).

- [ ] **Step 7: Checkpoint**

Pause; user commits.

---

## Task 10: Real-provider validation (gated) + full regression gate

**Files:**
- Test: `tests/test_phase4_real_provider.py` (opt-in, skipped without credentials)

- [ ] **Step 1: Write the opt-in real streaming test (Part 17)**

```python
import os, pytest
from models.router import DefaultModelRouter
from models.types import ModelRequest

@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="no GROQ_API_KEY")
def test_real_groq_stream_ordered_and_complete():
    from models.providers.groq import GroqModelProvider
    p = GroqModelProvider()  # reads GROQ_API_KEY
    got = []
    resp = DefaultModelRouter([p]).stream(ModelRequest(input="Count from 1 to 8, one per line."), got.append)
    assert resp.success and resp.content
    assert "".join(got) == resp.content        # chunks concatenate to the complete answer
    assert len(got) > 1                          # actually streamed (single paid call)
```

- [ ] **Step 2: Run it once (minimum necessary)**

Run: `GROQ_API_KEY=*** .venv/Scripts/python.exe -m pytest tests/test_phase4_real_provider.py -q`
Expected: PASS (one call). If Gemini/NVIDIA keys are present, optionally add the analogous one-call test. Confirm no duplicate-provider output appears after partial.

- [ ] **Step 3: Full backend regression gate**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: Phase 1 (`test_planner_trust_boundary.py`, `test_permission_and_continuation.py`), Phase 2 (`test_phase2_runtime_reliability.py`), Phase 3 (`test_phase3_latency.py`, `test_model_router.py`), and all new Phase 4 tests PASS. The 5 pre-existing `test_browser_capability.py` failures remain (they need a real MCP/Chrome env and are unchanged by Phase 4).

- [ ] **Step 4: Confirm no Phase 3 latency regression**

Run: `MAMBA_TIMING=1 .venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -q`
Expected: PASS — the arithmetic fast path, discovery gate, and fallback tests are untouched.

- [ ] **Step 5: Checkpoint**

Pause; user commits.

---

## Task 11: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (new §23.4, after the Phase 3 §23.3)

- [ ] **Step 1: Add §23.4 "Streaming (Phase 4)"**

Document, concisely: optional provider `stream()` selected by `capabilities["streaming"]`; the ambient `core/streaming.py` sink bound by `Brain.run` on the worker (mirrors cancellation, no signature churn into executor/skills); attach point is `AnalyzeSkill` answer synthesis only; progress (stage) vs deltas (tokens) kept separate; router first-token fallback gate (no provider switch mid-stream, cancel never falls back, permanent-before-first-token stops); complete path remains authoritative; partial never captured to memory; transport event vocabulary (`delta`/`progress` + authoritative `transcription`/`turnComplete`/`cancelled`) over the existing `/live` WS only — `/api/chat` remains non-streaming JSON because no consumer needs streamed HTTP (approved adjustment 1); voice unchanged (Phase 5 owns it). Add the `MAMBA_*` note only if a new env var is introduced (`MAMBA_STREAM_COALESCE_MS` if the coalescing window is made tunable).

- [ ] **Step 2: Update the repo map + test line in Appendix A**

Add `core/streaming.py`, `models/providers/_openai_sse.py`; bump the dated test summary to include the new `test_phase4_*` counts and note UI validation is `npx tsc`/`npm run build` + manual (no frontend test runner).

- [ ] **Step 3: Checkpoint**

Pause; user commits.

---

## Self-Review (run after writing, before handoff)

**1. Spec coverage.** Part 1 inspection → captured in the Ground Truth above (executors should re-verify anchors moved). Part 2 abstraction/capability → Tasks 2–5. Part 3 chunks-safe + memory-after-complete → Tasks 3–4 (complete ModelResponse), 6, 16/Part 13 verified in Task 7 Step 5/Task 6. Part 4 transport (SSE + structured events, no new server/runtime) → Task 8. Part 5 UI incremental → Task 9. Part 6 progress vs tokens → Task 8 (separate `delta` vs `progress`) + Task 9. Part 7 tool execution → progress only, streaming attaches at answer synthesis only (Task 6), no tool internals streamed (design rule). Part 8 cancellation → Task 7 (deterministic CANCELLED) + Task 5 (cancel never falls back). Part 9 stream failure → Tasks 3–5 (partial surfaced, never masked) + Task 9 (distinguish partial vs failed). Part 10 fallback → Task 5 first-token gate. Part 11 voice → Task 8 Step 3 (voice branch untouched, byte-compatible) + Task 9 Step 6 check #7. Part 12 progress callback → reuse `on_progress` + `stage` (Task 8). Part 13 memory → complete-only capture (Tasks 6, 7). Part 14 security → Task 7 Step 5 + never authority in UI. Part 15 performance → coalescing sink (Task 8) + rAF batching (Task 9 Step 2). Part 16 testing → Tasks 1–9 + 10 (23 scenarios). Part 17 real provider → Task 10. Part 18 frontend validation → Task 9 Steps 5–6. IMPORTANT DESIGN RULE (complete path always valid) → default `stream` raises + capability gate + degrade-to-invoke (Tasks 2, 5) + `/api/chat` left untouched (Task 8, approved adjustment 1). **Adjustment 2 (provisional deltas)** → Task 8 Step 3 flush-before-authoritative-frame ordering + Task 9 Step 2 reconcile-on-finalize. **Adjustment 3 (scope)** → attach point stays `skills/analyze.py` only. All 16 report items map to Task outputs.

**2. Placeholder scan.** No "TBD"/"similar to Task N"/bare "add error handling". Every code step shows real code. `TaskInput`/`SkillInput` construction fields are verified against `tasks/types.py:12-32` and `skills/types.py:19-21`. Gemini `_build_generate_kwargs` extraction is explicitly described as a behavior-preserving refactor with a verification step. Frontend `liveState`/`TranscriptEntry`/`audio.ts` handler fields are anchored to verified line numbers from the codebase survey.

**3. Type consistency.** `stream(request, on_text) -> ModelResponse` identical across provider base (Task 2), gemini (3), groq/nvidia (4), router (5). `set_current_sink/reset_current_sink/current_sink/emit_token` names consistent in Tasks 1/6/7/8. `stream_sink` param name consistent in Brain/runtime/transport (7/8). Frontend `onDelta`/`onTurnComplete`/`sendCancel` used consistently (9). `ModelResponse`/`SkillOutput`/`Observation`/`ExecutionResult` field names per `models/types.py`, `skills/types.py`, `core/types.py`.
