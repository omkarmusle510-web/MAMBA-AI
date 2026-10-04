"""Phase 3 latency optimization tests (instrumentation, discovery gate,
fast path, memory gating, provider routing/fallback)."""
from __future__ import annotations

import time

import core.timing as timing
from core.brain import Brain
from core.types import ExecutionPlan, PlanStep, ResultStatus
from tasks.executor import TaskExecutor
from tests.test_core_lifecycle import EchoTaskHandler, StaticPlanner


class _StubProjectContext:
    name = "proj"

    def format_summary(self) -> str:
        return "stub project context"


def _fake_brain(monkeypatch):
    """Brain with a deterministic single-step plan and no real discovery/LLM."""
    import core.project as project_mod

    monkeypatch.setattr(
        project_mod, "discover_project", lambda *a, **k: _StubProjectContext()
    )
    plan = ExecutionPlan(steps=(PlanStep(description="Say hi", intent="greet"),))
    executor = TaskExecutor(handlers={"greet": EchoTaskHandler(), "respond": EchoTaskHandler()})
    return Brain(planner=StaticPlanner([plan]), executor=executor)


# ── Part 1: instrumentation ──


def test_span_accumulates_without_changing_value_semantics(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    meta: dict = {}
    with timing.span(meta, "planning"):
        time.sleep(0.01)
    with timing.span(meta, "planning"):
        time.sleep(0.01)
    lat = timing.read(meta)
    assert lat["planning"] >= 18.0
    assert lat["planning"] == round(lat["planning"], 1)


def test_instrumentation_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", False)
    meta: dict = {}
    with timing.span(meta, "planning"):
        pass
    assert "latency_ms" not in meta


def test_latency_metadata_populated_when_enabled(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    brain = _fake_brain(monkeypatch)
    result = brain.run("say hi")
    lat = result.metadata.get("latency_ms")
    assert isinstance(lat, dict)
    assert "request_total" in lat
    assert "intake" in lat
    assert all(isinstance(v, (int, float)) for v in lat.values())
    assert result.status == ResultStatus.COMPLETED


def test_latency_metadata_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", False)
    brain = _fake_brain(monkeypatch)
    result = brain.run("say hi")
    assert result.metadata.get("latency_ms", {}) == {}
    assert result.status == ResultStatus.COMPLETED


# ── Part 2: project-discovery relevance gate (conservative) ──


from core.quickpath import is_clearly_project_irrelevant as irrelevant  # noqa: E402


def test_relevant_request_keeps_discovery():
    assert irrelevant("what's wrong with my project?", has_active_repository=True, metadata={}) is False
    assert irrelevant("explain this architecture", has_active_repository=False, metadata={}) is False
    assert irrelevant("find the bug in my code", has_active_repository=False, metadata={}) is False


def test_generic_request_skips_discovery():
    assert irrelevant("what time is it?", has_active_repository=False, metadata={}) is True
    assert irrelevant("what is 15 * 28?", has_active_repository=False, metadata={}) is True
    assert irrelevant("remember my favorite color is blue", has_active_repository=False, metadata={}) is True


def test_ambiguous_request_is_conservative():
    # Not a clear arithmetic/time/memory/greeting shape and no project token:
    # uncertain, so retain discovery (safe default).
    assert irrelevant("explain the tradeoffs", has_active_repository=False, metadata={}) is False


def test_active_repository_forces_retention():
    assert irrelevant("what time is it?", has_active_repository=True, metadata={}) is False


def test_discovery_skipped_for_generic_request_end_to_end(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    brain = _fake_brain(monkeypatch)
    result = brain.run("what is 15 * 28?")
    lat = result.metadata.get("latency_ms", {})
    assert "project_discovery" not in lat  # gate skipped it
    assert result.status == ResultStatus.COMPLETED


def test_discovery_retained_for_project_request_end_to_end(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    brain = _fake_brain(monkeypatch)
    result = brain.run("what's the architecture of this project?")
    lat = result.metadata.get("latency_ms", {})
    assert "project_discovery" in lat  # retained for project-relevant request


# ── Part 3: simple-request fast path ──


from core.cancellation import CancellationToken  # noqa: E402
from core.context import ExecutionContext  # noqa: E402
from core.quickpath import build_fast_plan, evaluate_arithmetic  # noqa: E402
from core.types import UserRequest  # noqa: E402


def test_arithmetic_resolved_deterministically():
    assert evaluate_arithmetic("what is 15 * 28?").strip().endswith("420")


def test_arithmetic_rejects_non_math_and_dangerous():
    assert evaluate_arithmetic("delete file.txt") is None
    assert evaluate_arithmetic("what is recursion?") is None
    # exponent blowup is not matched as pure additive/multiplicative arithmetic
    assert evaluate_arithmetic("what is 2 ** 999999?") is None


def test_fast_plan_rejects_destructive_and_external():
    assert build_fast_plan(UserRequest(goal="delete file.txt")) is None
    assert build_fast_plan(UserRequest(goal="open example.com")) is None
    assert build_fast_plan(UserRequest(goal="send an email to bob")) is None
    assert build_fast_plan(UserRequest(goal="search the web for cats")) is None


def test_fast_plan_rejects_memory_write_and_recall():
    # memory ops must keep the full path (capture / retrieval)
    assert build_fast_plan(UserRequest(goal="remember my favorite color is blue")) is None
    assert build_fast_plan(UserRequest(goal="what do you remember about my color")) is None


def test_fast_plan_accepts_safe_simple():
    plan = build_fast_plan(UserRequest(goal="what is 15 * 28?"))
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].intent == "calculate"
    assert plan.steps[0].metadata["resolved_text"].endswith("420")


def test_fast_path_end_to_end_skips_planning(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    brain = _fake_brain(monkeypatch)
    result = brain.run("what is 15 * 28?")
    assert result.status == ResultStatus.COMPLETED
    assert "420" in (result.output or "")
    lat = result.metadata.get("latency_ms", {})
    # fast path: no planning model call, no project discovery, no memory retrieve
    assert "planning" not in lat
    assert "project_discovery" not in lat
    assert "memory_retrieve" not in lat


def test_fast_path_respects_cancellation(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", True)
    brain = _fake_brain(monkeypatch)
    token = CancellationToken()
    token.cancel()
    result = brain.run("what is 15 * 28?", cancel_token=token)
    assert result.status == ResultStatus.CANCELLED


def test_fast_path_does_not_bypass_permission(monkeypatch):
    monkeypatch.setattr(timing, "_ENABLED", False)
    brain = _fake_brain(monkeypatch)
    brain.permissions = object()  # truthy so the permission block runs
    calls: dict[str, int] = {}

    def _spy(self, step, context):
        calls["n"] = calls.get("n", 0) + 1
        return True, "", False

    monkeypatch.setattr(Brain, "_evaluate_permission", _spy)
    result = brain.run("what is 15 * 28?")
    assert result.status == ResultStatus.COMPLETED
    assert calls.get("n") == 1  # permission was still evaluated on the fast path


# ── Part 6: memory retrieval gating (conservative, Memory V2 untouched) ──


class _SpyMemory:
    def __init__(self) -> None:
        self.retrieve_calls = 0

    def retrieve(self, *a, **k):
        self.retrieve_calls += 1
        return None

    def remember(self, *a, **k):
        return None

    def close(self) -> None:
        pass


def _brain_with_spy(monkeypatch):
    import core.project as project_mod

    monkeypatch.setattr(project_mod, "discover_project", lambda *a, **k: _StubProjectContext())
    spy = _SpyMemory()
    plan = ExecutionPlan(steps=(PlanStep(description="Say hi", intent="greet"),))
    executor = TaskExecutor(handlers={"greet": EchoTaskHandler()})
    brain = Brain(planner=StaticPlanner([plan]), executor=executor, memory=spy)
    return brain, spy


def test_memory_retrieval_skipped_for_arithmetic(monkeypatch):
    brain, spy = _brain_with_spy(monkeypatch)
    ur = UserRequest(goal="what is 15 * 28?")
    ctx = ExecutionContext.from_request(ur)
    brain._retrieve_memory(ctx, ur)
    assert spy.retrieve_calls == 0


def test_memory_retrieval_kept_for_recall(monkeypatch):
    brain, spy = _brain_with_spy(monkeypatch)
    ur = UserRequest(goal="what do you remember about my favorite color")
    ctx = ExecutionContext.from_request(ur)
    brain._retrieve_memory(ctx, ur)
    assert spy.retrieve_calls == 1


def test_memory_retrieval_kept_for_ordinary_request(monkeypatch):
    brain, spy = _brain_with_spy(monkeypatch)
    ur = UserRequest(goal="list the files in the current directory")
    ctx = ExecutionContext.from_request(ur)
    brain._retrieve_memory(ctx, ur)
    assert spy.retrieve_calls == 1  # uncertain -> retain (safe)


# ── Part 5: provider routing / fallback + cancellation safety ──


import pytest  # noqa: E402

import models.router as router_mod  # noqa: E402
from core.cancellation import MambaCancelledError  # noqa: E402
from models.router import DefaultModelRouter, _classify_transient  # noqa: E402
from models.types import ModelRequest  # noqa: E402
from tests.test_model_router import FakeProvider  # noqa: E402


class _Recorder(FakeProvider):
    """FakeProvider that also counts invoke() calls, to prove a branch was not reached."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        return super().invoke(request)


def _req() -> ModelRequest:
    return ModelRequest(input="hello")


def test_classify_transient_by_status():
    assert _classify_transient(RuntimeError("upstream HTTP 429 slow down")) is True
    assert _classify_transient(RuntimeError("upstream HTTP 503 unavailable")) is True
    assert _classify_transient(RuntimeError("upstream HTTP 400 bad request")) is False
    assert _classify_transient(RuntimeError("upstream HTTP 401 denied")) is False
    assert _classify_transient(RuntimeError("upstream HTTP 404 model")) is False


def test_classify_transient_by_words():
    assert _classify_transient(RuntimeError("request timed out")) is True
    assert _classify_transient(RuntimeError("rate limit exceeded")) is True
    assert _classify_transient(RuntimeError("invalid api key provided")) is False
    assert _classify_transient(RuntimeError("model not found")) is False


def test_classify_transient_unknown_is_bounded_transient():
    # Uncertain errors are treated as transient-but-bounded, never permanent, so
    # a genuine blip still gets one fallback without ever hiding the failure.
    assert _classify_transient(RuntimeError("some unclassified blip")) is True


def test_classify_transient_cancellation_is_not_transient():
    assert _classify_transient(MambaCancelledError("execution cancelled")) is False


def test_transient_failure_falls_back_and_marks_metadata():
    primary = FakeProvider("p1", "m1", fail_with=RuntimeError("HTTP 503 overloaded"))
    backup = FakeProvider("p2", "m2")
    router = DefaultModelRouter([primary, backup])
    resp = router.invoke(_req())
    assert resp.success is True
    assert resp.provider == "p2"
    assert resp.metadata.get("fallback_from_primary") is True
    assert resp.metadata.get("provider_attempts") == 2


def test_permanent_failure_stops_without_trying_remaining():
    primary = FakeProvider("p1", "m1", fail_with=RuntimeError("HTTP 401 invalid api key"))
    backup = _Recorder("p2", "m2")
    router = DefaultModelRouter([primary, backup])
    with pytest.raises(RuntimeError):
        router.invoke(_req())
    assert backup.calls == 0  # never burn the backup on a permanent failure


def test_cancellation_never_triggers_fallback():
    primary = FakeProvider("p1", "m1", fail_with=MambaCancelledError("execution cancelled"))
    backup = _Recorder("p2", "m2")
    router = DefaultModelRouter([primary, backup])
    with pytest.raises(MambaCancelledError):
        router.invoke(_req())
    assert backup.calls == 0  # cancellation is not a retryable failure


def test_transient_fallback_is_bounded_by_total_timeout(monkeypatch):
    # Every candidate is transient, but the wall-clock budget is already spent:
    # the loop must stop trying further providers and surface the original error.
    monkeypatch.setattr(router_mod, "_TOTAL_TIMEOUT", -1.0)
    primary = FakeProvider("p1", "m1", fail_with=RuntimeError("HTTP 503 overloaded"))
    backup = _Recorder("p2", "m2")
    router = DefaultModelRouter([primary, backup])
    with pytest.raises(RuntimeError):
        router.invoke(_req())
    assert backup.calls == 0
