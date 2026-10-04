# Phase 3 — Latency Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce unnecessary end-to-end latency in Mamba's execution path (project discovery, planning, memory, provider routing) while preserving correctness, security, permissions, verification, memory behavior, provider independence, and Phase 2 runtime reliability.

**Architecture:** Instrument the real execution boundaries first, measure a baseline with deterministic fakes, then apply three measurement-gated optimizations that reuse existing Mamba structures: (1) a conservative project-discovery relevance gate, (2) a narrow simple-request fast path that reuses the existing `_execute_step` permission→execute→verify pipeline as a single-step seeded plan, and (3) a bounded transient/non-transient provider fallback in `DefaultModelRouter.invoke` that never treats cancellation as a retryable failure. No new top-level layer is introduced; Brain, TaskExecutor, Skills, ModelRouter, Permissions, Verification, and Memory keep their current roles.

**Tech Stack:** Python 3.12.9, dataclasses, `contextlib`, `time.perf_counter`, `ast` (safe arithmetic), `threading`, pytest 9.1.1 (no pytest-asyncio; async via `asyncio.run` + FastAPI `TestClient`). Test runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** MAMBA AI — PHASE 3: LATENCY OPTIMIZATION (attached): `C:\Users\Ḥ\.qoder\tmp\C--mamba\attachments\74c3fcd0-a0e3-4abc-9a7b-47e7b918cdee\6c239c13-83fc-43c7-9595-b51af33d2edb.txt`

## Global Constraints

- **Do NOT redesign Mamba.** Do NOT introduce a new top-level layer. Do NOT replace Core, Brain, TaskExecutor, Skills, Tools, ModelRouter, Permissions, Verification, or Memory architecture (Spec §Master Architecture).
- **Invariants preserved** (Spec §Part 10): `Planner ≠ Authority`, `Planner ≠ Permission`, `Planner ≠ Approval`, `Planner ≠ Verification`; `Execution ≠ Success`, `Observation ≠ Verification`; `Cancellation ≠ Failure`, `Cancellation ≠ Success`.
- **Fast path MUST NOT bypass** (Spec §Part 3): permissions, permission metadata, target binding, verification, cancellation, execution state, memory controls, authoritative security checks. Destructive / externally-visible actions never become "fast" (e.g. "Delete file.txt" stays on the authoritative path).
- **Fallback must never** (Spec §Part 4/5): turn cancellation into retry, retry destructive operations, retry indefinitely, hide the original failure, produce false success, bypass permissions or verification.
- **Never log sensitive content** (Spec §Part 1/5): no user text, credentials, API keys, page contents, or memory contents for timing. Record durations, counts, and safe identifiers only.
- **No premature optimization** (Spec §Part 9): no global/response caching, speculative parallelism, extra Brain workers, distributed execution, Redis, message queues, background jobs, vector-cache, model preloading, or telemetry platform. Instrumentation must be lightweight, optional, structured, deterministic.
- **Measure first** (Spec §Implementation Principle): do not implement any optimization until instrumentation + baseline exist. Drop any optimization that yields <5% real improvement but adds complexity (§Part 12).
- **Provider independence** (Spec §Part 4): do NOT hardcode a permanent provider; do NOT add providers; do NOT repeatedly probe providers.
- **Git:** the implementer runs **no** git commands. The user performs all checkpoints. Each "checkpoint" step below means: *pause and let the user commit; do not run git yourself* (Spec §Git/Repository Rules: "Do NOT: commit / push / … Leave Git checkpointing to the user.").
- **STOP after Phase 3.** Do not begin Phase 4.

---

## File Structure

Responsibilities for the files Phase 3 touches. New files are tiny and single-purpose; changes to existing files are localized to the boundaries identified by measurement.

**New files:**
- `core/timing.py` — the entire latency instrument. A module toggle + a `span()` context manager + `mark()` that accumulate JSON-safe float durations into an execution's `request.metadata["latency_ms"]`. No timers anywhere else.
- `core/quickpath.py` — the single shared classifier used by BOTH the project-discovery gate and the simple-request fast path (so Parts 2/3/7 share one signal instead of two brains). Returns a deterministic single-step plan (`respond`/`calculate`) or `None`. Also holds the safe `ast` arithmetic evaluator.

**Modified files (what each gains):**
- `core/types.py` — `ExecutionResult` gains one defaulted `metadata: dict` field so instrumentation is inspectable on the returned result without changing any positional call.
- `core/state.py` — `ExecutionRecord.to_result` copies `request.metadata["latency_ms"]` (when present) into `ExecutionResult.metadata`; `to_dict`/`from_dict` carry `metadata`.
- `core/brain.py` — wire `timing` around intake / context / project discovery / memory-retrieve / planning-per-cycle / execute / verify / memory-write / total; defer `discover_project()` behind the relevance gate; gate pre-plan memory *retrieval*; dispatch the fast path after approval/correction handling; add `seed_plan` to `_execution_loop`.
- `core/project.py` — add `cached_discover_project()` (a root+HEAD-keyed memo, used ONLY if baseline shows retained discovery still dominates; otherwise unused).
- `models/router.py` — `invoke()` re-raises cancellation immediately, classifies transient vs permanent, breaks on permanent, bounds total fallback time, records safe attempt metadata.
- `agents/planning_agent.py` — re-raise `MambaCancelledError` in `reason()` so router's new re-raise is not swallowed into a fake planning failure.

**Tests:**
- `tests/bench_phase3_latency.py` — deterministic fake-provider / fake-discovery latency harness; prints before/after tables (not collected as a unit test; run explicitly).
- `tests/test_phase3_latency.py` — the 20 focused Phase 3 scenarios.

---

## Task 1: Latency instrumentation core

**Files:**
- Create: `core/timing.py`
- Modify: `core/types.py:154-172` (`ExecutionResult`)
- Modify: `core/state.py:91-123` (`to_result`, `to_dict`, `from_dict`)
- Test: `tests/test_phase3_latency.py` (created here, extended by later tasks)

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `core.timing.is_enabled() -> bool`
  - `core.timing.set_enabled(flag: bool) -> None`
  - `core.timing.span(metadata: dict, name: str) -> ContextManager[None]` — adds elapsed ms under `metadata["latency_ms"][name]` (accumulates across repeated spans with the same name).
  - `core.timing.mark(metadata: dict, name: str, dt_seconds: float) -> None` — record an already-measured duration.
  - `core.timing.read(metadata: dict) -> dict[str, float]` — return the latency dict (never raises).
  - `ExecutionResult.metadata: dict[str, Any]` (defaulted field, appended last).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase3_latency.py
import time
import core.timing as timing


def test_span_accumulates_without_changing_value_semantics(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    meta: dict = {}
    with timing.span(meta, "planning"):
        time.sleep(0.01)
    with timing.span(meta, "planning"):
        time.sleep(0.01)
    lat = timing.read(meta)
    assert lat["planning"] >= 18.0          # two ~10ms laps accumulate
    assert lat["planning"] == round(lat["planning"], 1)  # JSON-safe float


def test_instrumentation_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", False)
    meta: dict = {}
    with timing.span(meta, "planning"):
        pass
    assert "latency_ms" not in meta          # behavior unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.timing'`.

- [ ] **Step 3: Write the instrumentation module**

```python
# core/timing.py
"""Lightweight, optional latency instrumentation for Mamba's execution path.

Enabled only when the MAMBA_TIMING environment variable is truthy (or via
set_enabled for tests). Records durations (milliseconds) and safe
identifiers only — never user text, credentials, page contents, or memory.
When disabled, every helper is a no-op, so instrumentation cannot change
program behavior.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

_ENABLED = os.environ.get("MAMBA_TIMING", "0").strip().lower() not in (
    "", "0", "false", "no", "off",
)


def is_enabled() -> bool:
    return _ENABLED


def set_enabled(flag: bool) -> None:
    global _ENABLED
    _ENABLED = bool(flag)


def read(metadata: dict[str, Any]) -> dict[str, float]:
    """Return the recorded latency map (empty when nothing recorded)."""
    lat = metadata.get("latency_ms")
    return lat if isinstance(lat, dict) else {}


def mark(metadata: dict[str, Any], name: str, dt_seconds: float) -> None:
    if not _ENABLED:
        return
    lat = metadata.setdefault("latency_ms", {})
    lat[name] = round(lat.get(name, 0.0) + dt_seconds * 1000.0, 1)


@contextmanager
def span(metadata: dict[str, Any], name: str) -> Iterator[None]:
    if not _ENABLED:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        mark(metadata, name, time.perf_counter() - t0)
```

- [ ] **Step 4: Make ExecutionResult carry the timings**

In `core/types.py`, add a defaulted `metadata` field as the LAST field of `ExecutionResult` (dataclass ordering: required fields already precede it) and include it in `to_dict`/`from_dict`:

```python
# after: error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```
```python
# in to_dict(), add alongside the existing keys:
            "metadata": self.metadata,
```
```python
# in from_dict(), add:
            metadata=dict(data.get("metadata", {})),
```

- [ ] **Step 5: Surface latency_ms in to_result**

In `core/state.py`, replace `to_result`'s return (currently `state.py:103-110`) so it copies the execution's `latency_ms` when present, without otherwise changing behavior:

```python
    def to_result(self, *, output: str | None = None) -> ExecutionResult:
        if self.state == ExecutionState.COMPLETED:
            status = ResultStatus.COMPLETED
        elif self.state == ExecutionState.FAILED:
            status = ResultStatus.FAILED
        elif self.state == ExecutionState.CANCELLED:
            status = ResultStatus.CANCELLED
        else:
            raise ValidationError(
                f"cannot build result while execution is in state {self.state.value}"
            )
        latency = self.request.metadata.get("latency_ms")
        result_metadata = {"latency_ms": latency} if latency else {}
        return ExecutionResult(
            execution_id=self.id,
            status=status,
            goal=self.request.goal,
            observations=tuple(self.observations),
            output=output,
            error=self.error,
            metadata=result_metadata,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: User checkpoint** — pause; the user commits Task 1. Do not run git.

---

## Task 2: Wire instrumentation into Brain boundaries

**Files:**
- Modify: `core/brain.py:578-741` (`_run`), `1046-1163` (`_execution_loop`), `1165-1189` (`_reason`), `1247-1382` (`_execute_step` execute+verify), `998-1043` (`_retrieve_memory` project+memory spans), `2237-2311` (`_update_memory`)
- Test: `tests/test_phase3_latency.py`

**Interfaces:**
- Consumes: `core.timing` (Task 1).
- Produces: every `ExecutionResult` returned from `Brain.run` has `result.metadata["latency_ms"]` populated when timing is enabled; keys `intake, context_assembly, project_discovery, memory_retrieve, planning, execution, verification, memory_write, request_total`. When disabled, `metadata` is `{}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_phase3_latency.py
import core.timing as timing
from core.brain import Brain


def _fake_brain_factory():
    # Build a Brain with a deterministic fake provider (no network, no model load).
    ...  # reuse the fixture defined in tests/test_phase2_runtime_reliability.py


def test_latency_metadata_populated_when_enabled(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    brain = _fake_brain_factory()
    result = brain.run("what is 2 + 2?")
    lat = result.metadata.get("latency_ms")
    assert isinstance(lat, dict)
    assert "request_total" in lat
    assert all(isinstance(v, float) for v in lat.values())


def test_latency_metadata_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", False)
    brain = _fake_brain_factory()
    result = brain.run("what is 2 + 2?")
    assert result.metadata.get("latency_ms", {}) == {}
```

Fill `_fake_brain_factory` in Step 3 by wiring the existing `FakeModelProvider`/router used in the Phase 2 suite; the fake must not touch the network or load SentenceTransformer.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k latency_metadata -v`
Expected: FAIL — `request_total` missing (spans not wired yet).

- [ ] **Step 3: Wrap the boundaries**

Use `user_request.metadata` as the timing sink so it flows into `to_result` unchanged. Import at top of `core/brain.py`:

```python
from . import timing
```

In `_run` (starts `brain.py:578`): create the sink and total span right after intake succeeds, and record boundaries. Concretely, replace the intake guard block ending and subsequent sections so the structure is (unchanged control flow, only spans added):

```python
    def _run(self, request: str | UserRequest, _progress: Any) -> ExecutionResult:
        # ── 1. Request Intake ──
        t_total = time.perf_counter()
        user_request = self._intake(request)
        if user_request is None:
            return ExecutionResult(...)          # unchanged early-return
        timing.mark(user_request.metadata, "intake", time.perf_counter() - t_total)

        # ... sections 1a, 1b, 1c, 1d, 1e unchanged ...

        # ── 2. Context Assembly ──
        t0 = time.perf_counter()
        context = ExecutionContext.from_request(user_request)
        timing.mark(user_request.metadata, "context_assembly", time.perf_counter() - t0)

        # ── 3. Memory Retrieval ──
        if _progress:
            _progress("Understanding...")
        if not self._retrieve_memory(context, user_request):
            timing.mark(user_request.metadata, "request_total", time.perf_counter() - t_total)
            return context.record.to_result()

        # ── 4. Observation-Driven Execution Loop ──
        try:
            res = self._execution_loop(context, user_request, on_progress=_progress)
        except MambaCancelledError:
            res = self._cancel_result(context, _progress)
        else:
            if _progress and res.status == ResultStatus.COMPLETED:
                _progress("Completed")
        timing.mark(user_request.metadata, "request_total", time.perf_counter() - t_total)
        self._record_turn_context(user_request, res)
        return res
```

Add `import time` to `core/brain.py` imports if absent.

In `_retrieve_memory` (`brain.py:998`), wrap the two heavy blocks (the `discover_project()` block and the retrieval block) with spans against `context.record.request.metadata` (same dict object as `user_request.metadata` once `from_request` copies it — verify at runtime; if it is a *copy*, span against `user_request.metadata` which is the sink that reaches the result):

```python
        with timing.span(user_request.metadata, "project_discovery"):
            # existing discover_project() block (Task 4 will gate its entry)
            ...
        with timing.span(user_request.metadata, "memory_retrieve"):
            # existing memory.retrieve block
            ...
```

In `_reason` (`brain.py:1165`), span the planner call:

```python
        with timing.span(self._current_metadata, "planning"):
            plan = self.planner.plan(context)
```

Because `_reason` lacks the request metadata directly, pass the sink: change `self._reason(context)` call site in `_execution_loop` (`brain.py:1082`) to `self._reason(context, user_request.metadata)` and add the parameter:

```python
    def _reason(self, context: ExecutionContext, metadata: dict | None = None) -> ExecutionPlan | None:
        metadata = metadata if metadata is not None else context.record.request.metadata
```

In `_execution_loop` (`brain.py:1073` loop), span execute and verify by wrapping the `self._execute_step(...)` call and the `self._verify(...)` call with `timing.span(user_request.metadata, "execution")` and `timing.span(user_request.metadata, "verification")` respectively. `_verify` is called inside `_execute_step`; simplest correct wiring is to span around `_execute_step` for "execution" and, in `_execute_step`, wrap the `verified, reason = self._verify(step, observation)` line (`brain.py:1382`) with `timing.span(context.record.request.metadata, "verification")` — but since request metadata may be a copy, prefer threading: add `metadata: dict | None = None` to `_execute_step` and pass `user_request.metadata` from `_execute_plan` (`brain.py:1213`), and from `_execution_loop`'s `_execute_plan` call. Thread the same object to keep accumulation correct.

In `_update_memory` (`brain.py:2237`), at its call site in `_execution_loop` (`brain.py:1159`) wrap:

```python
        with timing.span(user_request.metadata, "memory_write"):
            self._update_memory(context, user_request)
```

- [ ] **Step 4: Confirm the sink identity at runtime (guard against dict copies)**

Run a scratch check to ensure `context.record.request.metadata` is the SAME dict object as `user_request.metadata` (so marks land in the result). If `ExecutionContext.from_request` copies metadata, then thread one explicit `latency` sink dict from `_run` through `_retrieve_memory`, `_reason`, `_execute_step`, and `_update_memory`, and pass it to `mark()`; attach it to `context.record.request.metadata["latency_ms"]` at the end of `_run` before building the result. Adjust the spans accordingly. This is the single integration decision to verify in this step; document the chosen mechanism in a one-line code comment.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Regression — timing must not change behavior**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase2_runtime_reliability.py -v`
Expected: PASS (all 15). Cancellation/result-shape behavior is unchanged because `metadata` is additive and defaulted.

- [ ] **Step 7: User checkpoint** — pause; the user commits Task 2. Do not run git.

---

## Task 3: Baseline latency measurement (MEASURE BEFORE OPTIMIZING)

**Files:**
- Create: `tests/bench_phase3_latency.py`
- Record: baseline numbers into a short table at the top of this plan file under "Baseline".

**Interfaces:**
- Consumes: `Brain` (Tasks 1–2), a deterministic `FakeModelProvider` (returns a canned plan/answer instantly), a patched `discover_project` that sleeps a fixed ~40ms to emulate the real FS+4-git cost, a fake memory manager.
- Produces: printed table of per-request-class latency for the 10 representative classes (Spec §Part 8): simple conversation, arithmetic, simple explanation, memory recall, memory write, web search, project question, coding/project analysis, tool execution, browser request.

- [ ] **Step 1: Write the harness**

```python
# tests/bench_phase3_latency.py
"""Deterministic latency harness. Not a pytest test — run explicitly.
   Usage: MAMBA_TIMING=1 .venv/Scripts/python.exe tests/bench_phase3_latency.py
Uses a fake provider + a sleeping fake discover_project so numbers are
reproducible without network or model loading (Spec Part 8)."""
from __future__ import annotations
import time, statistics
import core.timing as timing
from core.brain import Brain
# import the same fake provider/router helpers the Phase 2 suite uses

REQUESTS = [
    ("simple conversation", "hello there"),
    ("arithmetic", "what is 15 * 28?"),
    ("simple explanation", "explain recursion"),
    ("memory recall", "what do you remember about my favorite color?"),
    ("memory write", "remember that my favorite color is blue"),
    ("web search", "search the web for python 3.12 release date"),
    ("project question", "what's the architecture of this project?"),
    ("coding analysis", "find the bug in my code"),
    ("tool execution", "list the files in the current directory"),
    ("browser request", "open example.com in the browser"),
]

def run(brain, goal, repeats=5):
    timings = []
    for _ in range(repeats):
        t = time.perf_counter()
        brain.run(goal)
        timings.append((time.perf_counter() - t) * 1000.0)
    return statistics.median(timings)

if __name__ == "__main__":
    timing.set_enabled(True)
    brain = build_fake_brain()          # wire fakes here
    for label, goal in REQUESTS:
        print(f"{label:>18}: {run(brain, goal):7.1f} ms")
```

`build_fake_brain()` must (a) register a `FakeModelProvider` whose `invoke` returns a canned valid plan JSON then a canned answer; (b) monkeypatch `core.project.discover_project` to `time.sleep(0.04)` then return a minimal `ProjectContext` so the *baseline* shows the discovery cost clearly.

- [ ] **Step 2: Capture the baseline**

Run: `MAMBA_TIMING=1 .venv/Scripts/python.exe tests/bench_phase3_latency.py`
Expected: a table. Record the per-request `request_total` and per-span values.

- [ ] **Step 3: Record baseline into the plan**

Paste the table under the "## Baseline" heading at the end of this document (create it). Note specifically: does every request pay `project_discovery` and `memory_retrieve` even for conversation/arithmetic? Does `planning` show two model calls for simple requests (plan + answer)? These confirm the three optimization targets before changing anything.

- [ ] **Step 4: User checkpoint** — pause; the user commits Task 3. Do not run git. No optimization code written yet.

---

## Task 4: Conservative project-discovery relevance gate (Part 2 / Part 7)

**Files:**
- Create: `core/quickpath.py` (shared classifier — first used here, reused in Task 5)
- Modify: `core/brain.py:998-1019` (`_retrieve_memory` project block) and add `Brain._project_context_needed`
- Optional Modify: `core/project.py` (memoization, only if baseline justifies)
- Test: `tests/test_phase3_latency.py`

**Interfaces:**
- Consumes: existing signals — `self._active_entities` (`repository`), `user_request.metadata` (`project`), `self.capabilities` registry, and the exclusion intent sets already declared in `brain.py` (`_MUTATING_INTENTS`, `_BROWSER_INTENTS`, `_CROSS_APP_TEXT_INTENTS`) plus `skills/mixed.py` intent frozensets.
- Produces:
  - `core.quickpath.is_clearly_project_irrelevant(goal: str, *, has_active_repository: bool, metadata: dict) -> bool` → only returns `True` for confidently generic requests; `False` on any uncertainty (spec's conservative default = retain discovery).
  - `Brain._project_context_needed(user_request) -> bool` (default `True`).

- [ ] **Step 1: Write the failing tests (A/B/C/D from Part 2)**

```python
# append to tests/test_phase3_latency.py
from core.quickpath import is_clearly_project_irrelevant as irrelevant

def test_relevant_request_keeps_discovery():          # (A)
    assert irrelevant("what's wrong with my project?", has_active_repository=True, metadata={}) is False
    assert irrelevant("explain this architecture", has_active_repository=False, metadata={}) is False

def test_generic_request_skips_discovery():           # (B)
    assert irrelevant("what time is it?", has_active_repository=False, metadata={}) is True
    assert irrelevant("what is 15 * 28?", has_active_repository=False, metadata={}) is True
    assert irrelevant("remember my favorite color is blue", has_active_repository=False, metadata={}) is True

def test_ambiguous_request_is_conservative():         # (C) retain
    assert irrelevant("explain the tradeoffs", has_active_repository=False, metadata={}) is False

def test_active_repository_forces_retention():        # (D)
    assert irrelevant("what time is it?", has_active_repository=True, metadata={}) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "relevant_request or generic_request or ambiguous or active_repository" -v`
Expected: FAIL — `ModuleNotFoundError: core.quickpath`.

- [ ] **Step 3: Implement the shared classifier**

```python
# core/quickpath.py
"""Shared, conservative classifier reused by the project-discovery gate
and the simple-request fast path (Parts 2/3/7 use ONE signal). No LLM,
no second planner. Prefers structure over keyword hacks; when uncertain
it returns the SAFE answer (project-relevant / not-a-fast-path)."""
from __future__ import annotations

import ast
import operator
import re

# Confidently-generic conversational shapes (short, no project/coding nouns).
_TIME = re.compile(r"\b(what|tell).{0,10}\b(time|date|day)\b", re.I)
_ARITHMETIC = re.compile(r"^\s*what\s+is\s+[-+/*().\d\s]+\??\s*$", re.I)
# Words that tie a request to project/code — presence forces retention.
_PROJECT_TOKENS = re.compile(
    r"\b(project|repo|repository|code|file|directory|function|class|bug|error|"
    r"architecture|build|test|commit|branch|module|source|stack trace|traceback)\b",
    re.I,
)
_MEMORY_WRITE = re.compile(r"^\s*(remember|note that|don't forget)\b", re.I)
_MEMORY_RECALL = re.compile(r"\b(what do you (remember|recall)|from memory|do you remember)\b", re.I)


def is_arithmetic(goal: str) -> bool:
    return bool(_ARITHMETIC.match(goal))


def is_memory_write(goal: str) -> bool:
    return bool(_MEMORY_WRITE.match(goal))


def is_memory_recall(goal: str) -> bool:
    return bool(_MEMORY_RECALL.search(goal))


def is_clearly_project_irrelevant(
    goal: str, *, has_active_repository: bool, metadata: dict
) -> bool:
    # Conservative: retain discovery whenever any project tie or prior
    # project work exists. Skip only for confidently generic requests.
    if has_active_repository:
        return False
    if metadata.get("project"):
        return False
    if _PROJECT_TOKENS.search(goal):
        return False
    if _TIME.search(goal) or is_arithmetic(goal) or is_memory_write(goal):
        return True
    # Any other phrasing is uncertain -> retain discovery (safe default).
    return False
```

The `_ARITHMETIC` gate is intentionally narrow (only `what is <pure math>`) so `explain the tradeoffs` remains uncertain→retained (Test C).

- [ ] **Step 4: Gate the discovery call in `_retrieve_memory`**

At `core/brain.py:1005-1019`, wrap the `discover_project()` block so it runs only when needed. Keep the exact existing body; only change its trigger:

```python
        project = (
            user_request.metadata.get("project")
            or self._active_entities.get("repository")
            or ""
        )
        from .quickpath import is_clearly_project_irrelevant
        needs_project = not is_clearly_project_irrelevant(
            user_request.goal,
            has_active_repository=bool(self._active_entities.get("repository")),
            metadata=user_request.metadata,
        )
        if needs_project and (
            not project or "project_context" not in context.record.request.metadata
        ):
            with timing.span(user_request.metadata, "project_discovery"):
                try:
                    from .project import discover_project
                    proj_ctx = discover_project()
                    if not project:
                        project = proj_ctx.name
                    context.record.request.metadata["project_context"] = proj_ctx.format_summary()
                    context.record.request.metadata["project_name"] = proj_ctx.name
                except Exception:
                    pass
```

When `needs_project` is False, no discovery runs and `project_discovery` span is absent (0 cost).

- [ ] **Step 5: Add `Brain._project_context_needed` (used by fast path Task 5)**

```python
    def _project_context_needed(self, user_request: UserRequest) -> bool:
        from .quickpath import is_clearly_project_irrelevant
        return not is_clearly_project_irrelevant(
            user_request.goal,
            has_active_repository=bool(self._active_entities.get("repository")),
            metadata=user_request.metadata,
        )
```

- [ ] **Step 6: Run tests + regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py tests/test_phase2_runtime_reliability.py -v`
Expected: PASS. Then confirm Part 2 behavior: project questions still receive `project_context` in the plan (existing project-understanding tests in the repo must stay green — run the existing project/capability suite).

- [ ] **Step 7: Re-measure**

Run: `MAMBA_TIMING=1 .venv/Scripts/python.exe tests/bench_phase3_latency.py`
Expected: simple conversation / arithmetic / memory write show NO `project_discovery` span; project question / coding analysis still show it. Record before/after in the Baseline table.

- [ ] **Step 8: Optional memoization — only if justified**

If the *retained* discovery cost on project-relevant requests still dominates (baseline shows >5% of total and repeats across turns), add to `core/project.py`:

```python
_DISCOVERY_CACHE: dict[tuple[str, str | None], ProjectContext] = {}

def cached_discover_project(root_path=None) -> ProjectContext:
    root = find_project_root(root_path)
    head = None
    try:
        git_state = get_git_state(root)
        head = git_state.head
    except Exception:
        head = None
    key = (str(root), head)
    hit = _DISCOVERY_CACHE.get(key)
    if hit is not None:
        return hit
    ctx = discover_project(root)
    _DISCOVERY_CACHE[key] = ctx
    return ctx
```

and switch the gated block to call `cached_discover_project()`. Invalidate naturally via the HEAD key so working-tree changes across commits are picked up. If the relevance gate alone yields the win, DO NOT add this (Spec §Part 9/Part 12: drop negligible optimizations that add complexity). State the decision in the plan's Baseline note.

- [ ] **Step 9: User checkpoint** — pause; the user commits Task 4. Do not run git.

---

## Task 5: Simple-request fast path (Part 3 / Part 7)

**Files:**
- Modify: `core/quickpath.py` (add deterministic arithmetic evaluator + `build_fast_plan`)
- Modify: `core/brain.py:717-734` (dispatch after referent resolution, before memory retrieval) and `_execution_loop` seed_plan parameter (`1046`, `1073`)
- Test: `tests/test_phase3_latency.py`

**Interfaces:**
- Consumes: `Brain._execution_loop` / `_execute_plan` / `_execute_step` (unchanged pipeline), `PlanStep`, `ExecutionPlan` (`core/types.py`), exclusion intent sets (`_MUTATING_INTENTS`, `_BROWSER_INTENTS`, `_CROSS_APP_TEXT_INTENTS`), Task 4 classifier.
- Produces:
  - `core.quickpath.evaluate_arithmetic(goal: str) -> str | None` — safe `ast`-based result, `None` if not a pure arithmetic expression (no `eval`).
  - `core.quickpath.build_fast_plan(user_request) -> ExecutionPlan | None` — a single `calculate`/`respond` `PlanStep` or `None` (never destructive/external/browser/project).

- [ ] **Step 1: Write the failing tests (Part 3 + safety)**

```python
# append to tests/test_phase3_latency.py
from core.quickpath import evaluate_arithmetic, build_fast_plan

def test_arithmetic_resolved_deterministically():
    assert evaluate_arithmetic("what is 15 * 28?").strip().endswith("420")

def test_arithmetic_refuses_non_math():
    assert evaluate_arithmetic("delete file.txt") is None
    assert evaluate_arithmetic("what is recursion?") is None

def test_fast_plan_rejects_destructive():            # (8) safeguards intact
    from core.types import UserRequest
    assert build_fast_plan(UserRequest(goal="delete file.txt")) is None
    assert build_fast_plan(UserRequest(goal="open example.com")) is None
    assert build_fast_plan(UserRequest(goal="send an email to bob")) is None

def test_fast_plan_accepts_safe_simple():
    from core.types import UserRequest
    plan = build_fast_plan(UserRequest(goal="what is 15 * 28?"))
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].intent in {"calculate", "respond"}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "arithmetic or fast_plan" -v`
Expected: FAIL — `evaluate_arithmetic` not defined.

- [ ] **Step 3: Add the safe arithmetic evaluator and plan builder**

Append to `core/quickpath.py`:

```python
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def _eval_node(node) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")   # names/calls/attrs are rejected

def evaluate_arithmetic(goal: str) -> str | None:
    if not is_arithmetic(goal):
        return None
    expr = re.sub(r"^\s*what\s+is\s+", "", goal, flags=re.I).strip().rstrip("?").strip()
    try:
        value = _eval_node(ast.parse(expr, mode="eval"))
    except Exception:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expr} = {value}"
```

Add the plan builder. The exclusion check runs against the *request*, and it is deliberately conservative — anything not provably simple returns `None` (falls to the normal authoritative path):

```python
from core.types import ExecutionPlan, PlanStep

# Intents/patterns that are NEVER fast-path eligible (external/destructive/project).
_UNSAFE = re.compile(
    r"\b(delete|remove|write|create|mkdir|make dir|move|copy|send|reply|email|message|"
    r"open|browse|visit|navigate|click|type|launch|run|execute|command|deploy|push|install)\b",
    re.I,
)

def build_fast_plan(user_request) -> ExecutionPlan | None:
    goal = user_request.goal.strip()
    if _UNSAFE.search(goal):
        return None                                   # destructive/external -> authoritative path
    answer = evaluate_arithmetic(goal)
    if answer is not None:
        step = PlanStep(
            description=f"Answer: {goal}",
            intent="calculate",
            metadata={"fast_path": True, "resolved_text": answer},
        )
        return ExecutionPlan(steps=(step,))
    return None   # only arithmetic is resolved deterministically here;
                  # other simple replies can be added only if measurement warrants
```

- [ ] **Step 4: Thread the resolved text through the handler without re-adding a model call**

The `calculate` fast-path step already has the answer in `metadata["resolved_text"]`. In `_execute_step`, before `self.executor.execute(...)` (`brain.py:1342`), add a narrow short-circuit so a `fast_path` + `resolved_text` step produces an `Observation` directly (this is the ONLY execution the fast path skips; permission/target-binding/cancellation checks above it still run first):

```python
        # Fast-path deterministic resolution: arithmetic already computed safely
        # (no eval); still passed capability + browser/cross-app binding + permission
        # checks above, and still goes through observation + verification below.
        if step.metadata.get("fast_path") and "resolved_text" in step.metadata:
            raise_if_cancelled()
            observation = Observation(
                step_id=step.id,
                content=step.metadata["resolved_text"],
                success=True,
                metadata={"fast_path": True},
            )
            context.add_observation(observation)
            # fall through to the existing post-step verification block
        else:
            try:
                observation = self.executor.execute(step, context)
                context.add_observation(observation)
            except MambaCancelledError:
                raise
            except CoreError as exc:
                ...  # existing handler unchanged
            except Exception as exc:
                ...  # existing handler unchanged
```

Keep the existing `raise_if_cancelled()` at `brain.py:1362` and the verification block unchanged — a `calculate`/`respond` intent is not in the force-verify sets, so `_needs_verification` returns False and no external verification is fabricated. Nothing here bypasses permissions: the permission block (`brain.py:1325-1338`) runs before this.

- [ ] **Step 5: Dispatch the fast path in `_run`**

Insert after section 1e (referent resolution, `brain.py:718-721`) and BEFORE section 2 context assembly, so approval/correction/pending handling still takes precedence:

```python
        # ── 1f. Simple-request fast path (Parts 3/7) ──
        from .quickpath import build_fast_plan
        if not self._project_context_needed(user_request):
            fast_plan = build_fast_plan(user_request)
            if fast_plan is not None:
                context = ExecutionContext.from_request(user_request)
                try:
                    res = self._execution_loop(context, user_request,
                                               on_progress=_progress, seed_plan=fast_plan)
                except MambaCancelledError:
                    res = self._cancel_result(context, _progress)
                timing.mark(user_request.metadata, "request_total", time.perf_counter() - _t_total)
                self._record_turn_context(user_request, res)
                return res
```

(`_t_total` is the `_run` total start captured in Task 2; if Task 2 named it differently, reuse that variable.)

- [ ] **Step 6: Add `seed_plan` to `_execution_loop`**

Change signature at `brain.py:1046` to `def _execution_loop(self, context, user_request, on_progress=None, seed_plan=None):`. Inside the first cycle, if `seed_plan` is provided use it instead of `self._reason`:

```python
        while cycles_used < self.max_cycles:
            cycles_used += 1
            raise_if_cancelled()
            if cycles_used == 1 and seed_plan is not None:
                context.transition_to(ExecutionState.PLANNING)
                context.attach_plan(seed_plan)
                context.transition_to(ExecutionState.EXECUTING)
                plan = seed_plan
            else:
                if on_progress:
                    on_progress("Planning...")
                with timing.span(user_request.metadata, "planning"):
                    plan = self._reason(context, user_request.metadata)
                if plan is None:
                    return context.record.to_result()
                context.transition_to(ExecutionState.EXECUTING)
            # ... remaining body (filter steps, execute, evaluate) unchanged ...
```

PLANNING→EXECUTING is an allowed transition (`state.py:35`), mirroring `_reason` (`brain.py:1180`). The seeded single step executes through the unchanged `_execute_plan`, so the fast path runs the SAME pipeline (permission → execute/short-circuit → observation → verify → cancel) the normal path does — it only skips project discovery, pre-plan memory retrieval, and the planning model call.

- [ ] **Step 7: Run tests + safety/regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py tests/test_phase2_runtime_reliability.py -v`
Expected: PASS. Then run the existing Phase 1 authority/security suite (Spec §Part 11 item 18) — must stay green.

- [ ] **Step 8: Cancellation + permission-through-fast-path tests (Part 11 items 6,7,9)**

```python
# append to tests/test_phase3_latency.py
def test_fast_path_respects_cancellation():
    # token already set -> even an arithmetic fast path returns CANCELLED, never COMPLETED
    ...
    assert result.status is ResultStatus.CANCELLED

def test_fast_path_does_not_bypass_permission(monkeypatch):
    # spy on Brain._evaluate_permission; arithmetic step is benign but the
    # permission evaluator MUST still be called (pipeline preserved).
    called = {}
    monkeypatch.setattr(Brain, "_evaluate_permission",
                        lambda self, step, ctx: called.setdefault("n", 0) or (True, "", False))
    ...  # brain.run("what is 15 * 28?")
    assert called.get("n") == 1
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "bypass or cancellation" -v` → PASS.

- [ ] **Step 9: Re-measure fast-path benefit**

Run: `MAMBA_TIMING=1 .venv/Scripts/python.exe tests/bench_phase3_latency.py`
Expected: `arithmetic` request shows `request_total` with NO `planning` span and NO `project_discovery`. Record before/after. If the win is negligible, revert Task 5 per Spec §Part 12 (do not keep complexity for <5%).

- [ ] **Step 10: User checkpoint** — pause; the user commits Task 5. Do not run git.

---

## Task 6: Memory retrieval gating (Part 6)

**Files:**
- Modify: `core/brain.py:1020-1043` (`_retrieve_memory` retrieval block)
- Modify: `core/quickpath.py` (expose `is_memory_recall`/`is_memory_write` — already added in Task 4)
- Test: `tests/test_phase3_latency.py`

**Interfaces:**
- Consumes: `self.memory`/`self._memory_manager.retrieve`, Task 4 helpers.
- Produces: `Brain._memory_retrieval_wanted(user_request) -> bool` (conservative: retrieval skipped ONLY for confidently simple non-recall requests). Capture (`_update_memory`) is NOT changed here.

- [ ] **Step 1: Write the failing tests (Part 6)**

```python
# append to tests/test_phase3_latency.py
from core.types import UserRequest

def test_arithmetic_skips_memory_retrieval(brain_spy):
    ok, spy = brain_spy("what is 15 * 28?")
    assert spy.retrieve_calls == 0

def test_recall_request_keeps_retrieval(brain_spy):
    ok, spy = brain_spy("what do you remember about my favorite color?")
    assert spy.retrieve_calls == 1

def test_memory_write_keeps_capture(brain_spy):
    ok, spy = brain_spy("remember that my favorite color is blue")
    assert spy.capture_calls == 1            # Part 6: "Remember X" must still write
```

`brain_spy` is a fixture wrapping the fake memory manager to count `retrieve`/capture calls.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "memory_retrieval or recall or memory_write" -v`
Expected: FAIL — `arithmetic_skips...` sees `retrieve_calls == 1` (currently unconditional).

- [ ] **Step 3: Add the gating helper**

```python
# core/brain.py, near _project_context_needed
    def _memory_retrieval_wanted(self, user_request: UserRequest) -> bool:
        from .quickpath import is_memory_recall, is_arithmetic
        # Explicit recall always needs memory. When uncertain, retrieve (safe).
        if is_memory_recall(user_request.goal):
            return True
        if is_arithmetic(user_request.goal):
            return False
        # Conservative default: do not skip unless confidently simple.
        return True
```

Arithmetic is the only confidently-simple, no-recall class for now; extending it risks changing Mamba's expected memory behavior (Spec §Part 6).

- [ ] **Step 4: Apply the gate**

Wrap the retrieval block in `_retrieve_memory` (`brain.py:1027-1043`) so it runs only when wanted:

```python
        if self.memory is None and self._memory_manager is None:
            return True
        if not self._memory_retrieval_wanted(user_request):
            return True
        with timing.span(user_request.metadata, "memory_retrieve"):
            # existing retrieve block unchanged (self._memory_manager.retrieve / self.memory.retrieve)
            ...
```

- [ ] **Step 5: Run tests + regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -v`
Expected: PASS. Memory V2 is unchanged; no new cache/DB introduced.

- [ ] **Step 6: Re-measure** — confirm `arithmetic` shows no `memory_retrieve` span. Record.

- [ ] **Step 7: User checkpoint** — pause; the user commits Task 6. Do not run git.

---

## Task 7: Provider routing/fallback latency + cancellation safety (Parts 4/5)

**Files:**
- Modify: `models/router.py:179-211` (`invoke`)
- Modify: `agents/planning_agent.py:383-393` (`reason` exception handling)
- Test: `tests/test_phase3_latency.py`

**Interfaces:**
- Consumes: `core.cancellation.MambaCancelledError` (re-raise, never swallow), provider exception messages (HTTP status codes emitted by `models/providers/*` as `"… HTTP 429 …"` / `"timed out"` / `"network error"`).
- Produces:
  - `router._classify_transient(exc) -> bool` (transient vs permanent).
  - `router._extract_status(exc) -> int | None`.
  - `invoke` now: (a) re-raises cancellation instantly, (b) breaks on permanent failures without burning remaining candidates, (c) bounds total fallback wall-time, (d) records `provider_attempts` / `fallback_from_primary` / `transient_fallback_count` in response metadata (safe identifiers only).

- [ ] **Step 1: Write the failing tests (Part 11 items 10–14)**

```python
# append to tests/test_phase3_latency.py
import pytest
from models.router import DefaultModelRouter, _classify_transient
from models.errors import ModelProviderError
from core.cancellation import MambaCancelledError
from models.types import ModelRequest

class _Fake:
    def __init__(self, name, exc=None, ok="ok"): self.name, self.exc, self.ok = name, exc, ok
    def invoke(self, req):
        if self.exc: raise self.exc
        return _resp(self.name, self.ok)

def test_transient_classification():
    assert _classify_transient(ModelProviderError("Groq API HTTP 429: slow down")) is True
    assert _classify_transient(ModelProviderError("Groq API request timed out after 60s")) is True
    assert _classify_transient(ModelProviderError("Groq API HTTP 401: invalid api key")) is False
    assert _classify_transient(ModelProviderError("Groq API HTTP 400: bad request")) is False

def test_cancellation_never_triggers_fallback():
    calls = []
    p1 = _Counting(_Fake("a", exc=MambaCancelledError()), calls)
    p2 = _Fake("b", ok="second")
    router = DefaultModelRouter([p1, p2])
    with pytest.raises(MambaCancelledError):
        router.invoke(ModelRequest(input="x"))
    assert calls == []                         # p2 (fallback) never attempted

def test_permanent_failure_does_not_try_remaining():
    p1 = _Fake("a", exc=ModelProviderError("HTTP 401: invalid api key"))
    p2 = _Flag()                               # raises if invoke() is called
    router = DefaultModelRouter([p1, p2])
    with pytest.raises(ModelProviderError):
        router.invoke(ModelRequest(input="x"))   # re-raised, original failure not hidden

def test_transient_failure_falls_back_bounded():
    p1 = _Fake("a", exc=ModelProviderError("HTTP 503: overloaded"))
    p2 = _Fake("b", ok="answer")
    router = DefaultModelRouter([p1, p2])
    resp = router.invoke(ModelRequest(input="x"))
    assert resp.content == "answer"
    assert resp.metadata.get("fallback_from_primary") is True
    assert resp.metadata.get("provider_attempts") == 2
```

Fill `_resp`, `_Counting`, `_Flag` with the same helpers the existing router tests use (import them; do not duplicate provider fakes that already exist).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "transient or cancellation_never or permanent or bounded" -v`
Expected: FAIL — `_classify_transient` not defined; cancellation currently swallowed and triggers fallback.

- [ ] **Step 3: Implement classification + bounded, cancellation-safe invoke**

At the top of `models/router.py`:

```python
import os, re, time
from core.cancellation import MambaCancelledError
```

Add module helpers:

```python
_TOTAL_TIMEOUT = float(os.environ.get("MAMBA_PROVIDER_TOTAL_TIMEOUT", "90"))
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_PERMANENT_STATUS = {400, 401, 403, 404, 405, 409, 413, 415, 422}
_STATUS_RE = re.compile(r"HTTP (\d{3})")
_TRANSIENT_WORDS = ("timed out", "timeout", "overloaded", "rate limit",
                    "try again", "network error", "temporarily", "connection refused")
_PERMANENT_WORDS = ("invalid api key", "unsupported model", "not found",
                    "invalid request", "permission denied", "unauthorized")

def _extract_status(exc: Exception) -> int | None:
    m = _STATUS_RE.search(str(exc))
    return int(m.group(1)) if m else None

def _classify_transient(exc: Exception) -> bool:
    if isinstance(exc, MambaCancelledError):
        return False
    text = str(exc).lower()
    status = _extract_status(exc)
    if status is not None:
        if status in _TRANSIENT_STATUS:
            return True
        if status in _PERMANENT_STATUS:
            return False
    if any(w in text for w in _TRANSIENT_WORDS):
        return True
    if any(w in text for w in _PERMANENT_WORDS):
        return False
    return True   # uncertain -> treat as transient but bounded (do not hide failure)
```

Rewrite `invoke` (`models/router.py:179-211`) preserving the success/fallback metadata shape and candidate selection:

```python
    def invoke(self, request: ModelRequest) -> ModelResponse:
        candidates = self.route_candidates(request)
        last_error: Exception | None = None
        last_response: ModelResponse | None = None
        attempts = 0
        deadline = time.monotonic() + _TOTAL_TIMEOUT if len(candidates) > 1 else None

        for i, provider in enumerate(candidates):
            attempts += 1
            try:
                response = provider.invoke(request)
            except MambaCancelledError:
                raise                                  # cancellation != fallback (Part 5)
            except Exception as exc:
                last_error = exc
                if not _classify_transient(exc):
                    raise                              # permanent: stop, do not burn candidates
                if deadline is not None and time.monotonic() >= deadline:
                    break                              # bounded transient fallback
                continue

            if response.success:
                if i > 0:
                    meta = dict(response.metadata)
                    meta["fallback_from_primary"] = True
                    meta["fallback_attempt"] = i
                    meta["provider_attempts"] = attempts
                    meta["primary_provider_error_class"] = "transient"
                    return ModelResponse(
                        content=response.content, provider=response.provider,
                        model=response.model, success=True, error=None, metadata=meta,
                    )
                return response          # primary success: unchanged (no perturbation of existing router tests)
            last_response = response

        if last_response is not None:
            return last_response
        if last_error is not None:
            raise last_error                           # original failure surfaced, never masked
        raise ModelRoutingError("all candidate model providers failed invocation")
```

This: preserves provider independence and the routing architecture; re-raises cancellation; avoids unnecessary serial 60s attempts on permanent errors; bounds total fallback wall-time; records safe attempt counts (no content/keys).

- [ ] **Step 4: Stop planning_agent from converting cancellation into a fake planning failure**

In `agents/planning_agent.py`, add a re-raise before the broad catch at `planning_agent.py:389`:

```python
        try:
            if hasattr(self._router, "invoke"):
                response = self._router.invoke(request)
            else:
                provider = self._router.route(request)
                response = provider.invoke(request)
        except MambaCancelledError:
            raise
        except Exception as exc:
            return AgentOutput(success=False, error=f"model invocation failed: {exc}")
```

Import `MambaCancelledError` at the top of `planning_agent.py` (`from core.cancellation import MambaCancelledError`). This keeps Phase 2's CANCELLED determinism intact now that the router re-raises cancellation instead of swallowing it.

- [ ] **Step 5: Run routing tests + regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -k "transient or cancellation or permanent or bounded" -v`
Expected: PASS. Then run the existing router/models suite (Spec §Part 10: provider failure handling must not regress) and the Phase 2 cancellation suite — both green.

- [ ] **Step 6: Re-measure routing latency under a failing primary**

Extend the bench with a "primary returns 503 then secondary ok" and a "primary returns 401" scenario. Expected: 503→secondary bounded and recorded; 401→fails fast without serial 60s × 5. Record before/after.

- [ ] **Step 7: User checkpoint** — pause; the user commits Task 7. Do not run git.

---

## Task 8: Full validation, before/after comparison, and regression gate

**Files:**
- Modify: Baseline table in this plan (before/after).
- Read-only: run the suites; fix any regression at its source (no git).

- [ ] **Step 1: Run focused Phase 3 tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase3_latency.py -v`
Expected: PASS (all Part 11 scenarios: discovery A/B/C/D, fast-path 4–9, routing 10–14, instrumentation 15–17).

- [ ] **Step 2: Run Phase 2 regression (worker/cancellation/shutdown/MCP)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase2_runtime_reliability.py -v`
Expected: PASS (15). Confirms no regression of Spec §Part 10.

- [ ] **Step 3: Run Phase 1 authority/security tests**

Run the existing security/authority tests (Spec §Part 11 item 18). Identify them first via the existing suite (e.g. `pytest tests -k "permission or authority or security"`), then confirm PASS. Fast path must not have bypassed any authority check (Spec §Part 3).

- [ ] **Step 4: Run the full backend suite**

Run: `.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS except any failures PRE-EXISTING before Phase 3. If a test newly fails, apply systematic-debugging: identify the root cause in the changed boundary, fix it, re-run. Do not silence the test.

- [ ] **Step 5: Re-run the latency bench and compare**

Run: `MAMBA_TIMING=1 .venv/Scripts/python.exe tests/bench_phase3_latency.py`
Expected vs Task 3 baseline: simple conversation / arithmetic drop (no project discovery, no/less planning, no memory retrieve); project/coding unchanged or slightly faster (memo if justified); failing-primary routing bounded. Fill the "Before / After / Δ%" row in the Baseline table for each of the 10 classes.

- [ ] **Step 6: Prune negligible optimizations (Spec §Part 12)**

If any task's measured improvement is <5% while adding complexity, revert that task's change (and its now-obsolete tests) at its source. Record each accept/reject decision in the report.

- [ ] **Step 7: User checkpoint** — pause; the user commits the validated Phase 3. Do not run git.

---

## Task 9: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (execution-efficiency note), `docs/PROJECT_UNDERSTANDING.md` if it describes the request path.

- [ ] **Step 1: Document only behavior that actually changed**

Add a concise subsection to `docs/ARCHITECTURE.md`: project-discovery relevance gating; simple-request fast path (arithmetic, executed through the same permission/verify/cancel pipeline; destructive actions never fast-pathed); provider routing transient/non-transient bounded fallback with cancellation-never-falls-back; and the optional `MAMBA_TIMING=1` latency instrumentation (records durations only, never user content). Note the env knobs `MAMBA_TIMING` and `MAMBA_PROVIDER_TOTAL_TIMEOUT`. Keep it short — this is a small optimization, not a performance treatise (Spec §Part 13).

- [ ] **Step 2: User checkpoint** — pause; the user commits docs. Do not run git.

---

## Baseline

**Captured in Task 3 (before any optimization).** Deterministic bench (`tests/bench_phase3_latency.py`) emulating the collaborators that dominate Mamba's per-request overhead; the emulated `discover_project` cost was aligned to the **real measured value on this machine: ~458–480 ms** per call (uncached FS walk + 4 git subprocesses).

Key structural finding confirmed by the "before" spans: **every** request — including `arithmetic`, `simple conversation`, `simple explanation` — pays `project_discovery` (~480 ms), `memory_retrieve` (~10 ms), `planning` (~300 ms) and an answer call (~250 ms). Project discovery is ~46% of a simple request's fixed cost and is entirely irrelevant to it. This is exactly what Parts 2/3/6 target.

| Request class | Before (ms) | After (ms) | Δ% | Notes |
|---|---|---|---|---|
| simple conversation | 1048.9 | 1048.2 | ~0% | uncertain → discovery retained (conservative) |
| arithmetic | 1048.3 | **5.9** | **−99.4%** | deterministic fast path: no discovery/retrieve/plan |
| simple explanation | 1047.9 | 1048.4 | ~0% | "explain …" → discovery retained (conservative) |
| memory recall | 1048.0 | 1048.4 | ~0% | must keep retrieval + discovery |
| memory write | 1048.9 | **567.2** | **−45.9%** | discovery gated off ("remember …" is project-irrelevant) |
| web search | 1049.0 | 1047.3 | ~0% | external/tool → discovery retained |
| project question | 1048.8 | 1047.8 | ~0% | KEEP discovery (correct — needed) |
| coding / project analysis | 1050.7 | 1048.0 | ~0% | KEEP discovery (correct — needed) |
| tool execution | 1048.6 | 1048.2 | ~0% | discovery retained |
| browser request | 1048.9 | 1047.4 | ~0% | never fast-pathed (external) — correct |

Per-span after: `arithmetic` — all spans absent except `request_total`≈5.9 ms and `memory_write`≈5 ms. `memory write` — `project_discovery` absent (0), rest unchanged. All other classes — identical to before (`project_discovery≈480`, `memory_retrieve≈10`, `planning≈300`, `execution≈250`, `memory_write≈5`).

The wins are concentrated exactly where the gate is **confident** the cost is irrelevant (arithmetic, memory-write). Every uncertain or project-relevant class keeps full discovery — the conservative design working as intended. Real uncached `discover_project()` on this machine measured 458–607 ms; the fast path removes it entirely for arithmetic, and the gate removes it for memory-write/greeting-shaped requests.

**Decisions — final resolution (measured):**
- `cached_discover_project()` (Task 4 Step 8) — **NOT implemented.** Measurement (locked #5): retained discovery is still ~480 ms across 8/10 classes, so the raw cost is real. But the only variants that would help those classes are a process-lifetime / global cache, which locked decision **#8 explicitly forbids** ("no global caching") and which risks feeding **stale project context** to the planner (a correctness regression the spec protects). A safe per-single-request memo yields ~0 here because replanning is 1 cycle (below). No low-complexity, constraint-safe cache exists → rejected on #8/constraint + correctness grounds, **not** on negligible measurement. Surfaced as the single open item for the user: accept the safe ~480 ms discovery cost, or authorize a separate TTL/invalidation cache as its own phase.
- Replanning cycle reduction (locked #6) — **NOT done, confirmed by measurement.** The accumulating `planning` span stayed at ~300 ms (a single cycle) for every one of the 10 classes; none triggered a replan loop. Reducing the cap of 10 therefore has **zero measured latency benefit** and would risk planning correctness. Cap left at 10; replanning frequency reported, not reduced.
- General simple-Q&A fast path (greeting/time/explanation beyond arithmetic) — **REJECTED during validation.** A first cut that fast-pathed short single-verb requests answered "Say hello" / "Inspect system hardware" locally, skipping the planner; this caused 6 regressions (milestone ordering, capability-metadata injection, canonical-execution and calendar tests). It was a de-facto "second brain." Reverted to arithmetic-only. This is the canonical "measurement did not justify the complexity" rejection.

---

## Self-Review (run after writing, before handoff)

**1. Spec coverage.** Part 1 instrumentation → Tasks 1–2. Part 2 project discovery → Task 4. Part 3 fast path → Task 5. Part 4 routing latency → Task 7. Part 5 fallback policy → Task 7. Part 6 fast path + memory → Task 6. Part 7 fast path + project context → Task 4 classifier shared with Task 5/6. Part 8 baseline → Tasks 3 & 8. Part 9 no premature optimization → memo is optional/gated (Task 4 Step 8), prune rule (Task 8 Step 6). Part 10 regression safety → Tasks 7/8 (cancellation re-raise, Phase 2/1 suites). Part 11 testing → the 20 named scenarios across Tasks 4–8. Part 12 validation → Task 8. Part 13 docs → Task 9. All 14 report items map to Task 8 outputs.

**2. Placeholder scan.** The `_fake_brain_factory` / `_resp` / `_Counting` / `_Flag` / `brain_spy` symbols instruct reuse of fakes that already exist in the Phase 2 and router test suites rather than duplicating them; the implementing agent must import/extend those. Any real `...` marks an EXISTING unchanged block, not a to-be-written one.

**3. Type consistency.** `timing.span/mark/read/set_enabled` names identical in Tasks 1/2/4/5/6. `build_fast_plan`/`evaluate_arithmetic`/`is_clearly_project_irrelevant`/`is_memory_recall`/`is_arithmetic` defined in Tasks 4–5 and consumed consistently. `ExecutionResult.metadata` and `to_result` copy added once (Task 1) and relied on everywhere. `MambaCancelledError` re-raise pattern identical in Task 7 Step 3 and Step 4. `_execution_loop(..., seed_plan=...)` signature matches the call added in Task 5 Step 5.
