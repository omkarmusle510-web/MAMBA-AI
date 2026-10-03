"""Focused tests for Phase 2 runtime-reliability behaviour.

Covers ONLY the new reliability guarantees introduced in Phase 2:
cooperative cancellation, the optional progress callback, the async/worker
boundary, MCP timeout/cancellation, and worker-boundary thread-safety.
Existing suites already cover planning, permissions, memory semantics and
the transport protocol, so those are not repeated here.

The suite is plain pytest (no pytest-asyncio is installed); async behaviour
is exercised with ``asyncio.run`` and the FastAPI ``TestClient``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from core.brain import Brain
from core.cancellation import (
    CancellationToken,
    MambaCancelledError,
    reset_current_token,
    set_current_token,
)
from core.context import ExecutionContext
from core.runtime import MambaRuntime
from core.types import ExecutionPlan, ExecutionResult, PlanStep, ResultStatus
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tests.test_core_lifecycle import EchoTaskHandler, StaticPlanner
from tools.browser.mcp import McpStdioClient, _CLOSED
from tools.browser.types import BrowserTargetError


def _runtime_for(plan: ExecutionPlan, handler) -> tuple[MambaRuntime, Brain]:
    executor = TaskExecutor(handlers={"greet": handler})
    brain = Brain(planner=StaticPlanner([plan]), executor=executor)
    return MambaRuntime(brain=brain), brain


# ── Part 6: cancellation is deterministic and never success ──


def test_cancellation_before_execution_yields_cancelled_not_completed():
    """A token cancelled before the run stops execution at the first boundary."""
    plan = ExecutionPlan(steps=(PlanStep(description="Say hello", intent="greet"),))
    handler = EchoTaskHandler()
    runtime, _ = _runtime_for(plan, handler)

    token = CancellationToken()
    token.cancel()
    result = runtime.run("Say hello", cancel_token=token)

    assert result.status == ResultStatus.CANCELLED
    assert result.status != ResultStatus.COMPLETED
    assert handler.executed_steps == []


def test_cancellation_mid_execution_stops_before_next_step():
    """Cancelling during a step prevents any subsequent step from executing."""
    token = CancellationToken()
    plan = ExecutionPlan(
        steps=(
            PlanStep(description="first", intent="greet"),
            PlanStep(description="second", intent="greet"),
        )
    )
    executed: list[str] = []

    class CancelAfterFirst:
        def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
            executed.append(task_input.description)
            token.cancel()
            return TaskOutput(content="ok", success=True)

    runtime, _ = _runtime_for(plan, CancelAfterFirst())
    result = runtime.run("do both", cancel_token=token)

    assert result.status == ResultStatus.CANCELLED
    assert executed == ["first"]


def test_cancelled_execution_is_not_verified_or_approved():
    """A cancelled run never carries a completed/verified observation outcome."""
    token = CancellationToken()
    plan = ExecutionPlan(steps=(PlanStep(description="Say hello", intent="greet"),))
    handler = EchoTaskHandler()
    runtime, _ = _runtime_for(plan, handler)
    token.cancel()

    result = runtime.run("Say hello", cancel_token=token)

    assert result.status == ResultStatus.CANCELLED
    assert not any(obs.metadata.get("verified") for obs in result.observations)


def test_mamba_cancelled_error_is_an_exception_subclass():
    """Per design, cancellation is an Exception that broad handlers must re-raise."""
    assert issubclass(MambaCancelledError, Exception)


# ── Part 3: progress callback is meaningful and optional ──


def test_progress_callback_receives_lifecycle_milestones():
    plan = ExecutionPlan(steps=(PlanStep(description="Say hello", intent="greet"),))
    handler = EchoTaskHandler()
    runtime, _ = _runtime_for(plan, handler)

    seen: list[str] = []
    result = runtime.run("Say hello", on_progress=seen.append)

    assert result.status == ResultStatus.COMPLETED
    assert "Planning..." in seen
    assert "Executing..." in seen
    assert "Completed" in seen


def test_progress_callback_is_optional_and_does_not_change_behaviour():
    plan = ExecutionPlan(steps=(PlanStep(description="Say hello", intent="greet"),))
    handler = EchoTaskHandler()
    runtime, _ = _runtime_for(plan, handler)

    result = runtime.run("Say hello")  # no on_progress, no cancel_token

    assert result.status == ResultStatus.COMPLETED
    assert len(handler.executed_steps) == 1


# ── Part 4: MCP request is bounded, cancellable, and id-safe ──


class _FakeStdin:
    def write(self, _data: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcess:
    """Stand-in child process that never answers, so waits must time out."""

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _client_with_fake_process(timeout: float = 5.0) -> McpStdioClient:
    client = McpStdioClient(["echo"], timeout=timeout)
    client._process = _FakeProcess()  # type: ignore[assignment]
    return client


def test_mcp_request_times_out_and_stops_waiting():
    """A silent child must not block forever: the timeout ends the wait."""
    client = _client_with_fake_process(timeout=0.2)
    try:
        with _raises_browser_error():
            client.request("tools/call", {"name": "noop"})
    finally:
        client._closed.set()


def test_mcp_request_is_cancellable_via_ambient_token():
    """An ambient cancellation unblocks a pending MCP request promptly."""
    client = _client_with_fake_process(timeout=30.0)
    token = CancellationToken()
    token.cancel()
    reset = set_current_token(token)
    try:
        import time

        start = time.monotonic()
        with _raises_browser_error():
            client.request("tools/call", {"name": "noop"})
        assert time.monotonic() - start < 5.0
    finally:
        reset_current_token(reset)
        client._closed.set()


def test_mcp_drops_responses_for_unknown_request_ids():
    """A late response for an id that is no longer pending is discarded."""
    client = _client_with_fake_process()
    # No request registered for id 999 — dispatch must not raise or store it.
    client._dispatch({"jsonrpc": "2.0", "id": 999, "result": {}})
    assert 999 not in client._pending


def test_mcp_delivers_response_to_matching_request_only():
    """Responses are routed by id, so one request cannot read another's reply."""
    import queue as _queue

    client = _client_with_fake_process()
    q1: _queue.Queue = _queue.Queue(maxsize=1)
    q2: _queue.Queue = _queue.Queue(maxsize=1)
    with client._pending_lock:
        client._pending[1] = q1
        client._pending[2] = q2

    client._dispatch({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}})

    assert q2.get_nowait()["id"] == 2  # type: ignore[index]
    assert q1.empty()


def test_mcp_wake_pending_unblocks_waiters_on_close():
    """Closing the connection wakes every pending request with the sentinel."""
    import queue as _queue

    client = _client_with_fake_process()
    q: _queue.Queue = _queue.Queue(maxsize=1)
    with client._pending_lock:
        client._pending[7] = q

    client._wake_pending()

    assert q.get_nowait() is _CLOSED
    assert client._pending == {}


class _raises_browser_error:
    """Context manager asserting a BrowserTargetError is raised."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert exc_type is BrowserTargetError, f"expected BrowserTargetError, got {exc_type}"
        return True


# ── Part 2: the async/worker boundary keeps blocking work off the loop ──


def test_chat_endpoint_runs_on_dedicated_worker_thread(tmp_path):
    """runtime.run must execute on the single worker thread, not the event loop."""
    from fastapi.testclient import TestClient

    from api.server import create_app

    seen_threads: list[str] = []

    class _StubRuntime:
        def run(self, request, **kwargs):
            seen_threads.append(threading.current_thread().name)
            return ExecutionResult(
                execution_id="x",
                status=ResultStatus.COMPLETED,
                goal="hi",
                observations=(),
                output="ok",
            )

        def shutdown(self) -> None:
            pass

    app = create_app(
        _StubRuntime(),  # type: ignore[arg-type]
        settings_path=tmp_path / "settings.json",
        reminders_path=tmp_path / "reminders.json",
    )
    client = TestClient(app)
    resp = client.post("/api/chat", json={"input": "hi"})

    assert resp.status_code == 200
    assert seen_threads and seen_threads[0].startswith("mamba-worker")
    assert seen_threads[0] != threading.main_thread().name


# ── Part 7: application shutdown is bounded and separate from cancellation ──


def test_brain_shutdown_stops_browser_and_closes_store():
    """shutdown() tears down owned resources without touching a cancel token."""
    stopped: list[str] = []
    closed: list[str] = []

    class _Session:
        def stop(self) -> None:
            stopped.append("session")

    class _BrowserHandler:
        @property
        def browser_session(self):
            return _Session()

    class _Store:
        def close(self) -> None:
            closed.append("store")

        def remember(self, *_a, **_k):
            return None

        def retrieve(self, *_a, **_k):
            return None

    executor = TaskExecutor(handlers={"browser_navigate": _BrowserHandler()})
    brain = Brain(planner=StaticPlanner([]), executor=executor, memory=_Store())

    brain.shutdown()

    assert stopped == ["session"]
    assert closed == ["store"]


# ── Part 8: memory stores are safe under concurrent access ──


def test_in_memory_store_survives_concurrent_writes():
    from memory.store import InMemoryStore
    from memory.types import MemoryEntry, MemoryQuery

    store = InMemoryStore()

    def write(i: int) -> None:
        store.store(MemoryEntry(content=f"shared token {i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(200)))

    result = store.retrieve(MemoryQuery(query="shared token", limit=1000))
    assert result.metadata["matched"] == 200


def test_persistent_store_survives_concurrent_writes(tmp_path):
    from memory.persistent import PersistentStore
    from memory.types import MemoryEntry, MemoryQuery

    store = PersistentStore(tmp_path / "mem.db")
    try:
        def write(i: int) -> None:
            store.store(MemoryEntry(content=f"row {i}", project="p"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(120)))

        result = store.retrieve(MemoryQuery(project="p", limit=1000))
        assert result.metadata["matched"] == 120
    finally:
        store.close()
