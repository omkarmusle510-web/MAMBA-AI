"""Phase 4 transport tests: /live delta frames, lifecycle frames, JSON endpoint stability."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import core.streaming as streaming
from api.server import create_app
from core.brain import Brain
from core.runtime import MambaRuntime
from core.types import ExecutionPlan, ExecutionResult, PlanStep, ResultStatus
from tasks.executor import TaskExecutor
from tasks.types import TaskOutput
from tests.test_core_lifecycle import StaticPlanner


class _StreamingHandler:
    """Pushes provisional deltas through the ambient sink, then answers completely."""

    def run(self, task_input, context) -> TaskOutput:
        streaming.emit_token("part1")
        streaming.emit_token("part2")
        return TaskOutput(content="part1part2", success=True)


def _app_for(goal: str, intent: str, tmp_path: Path):
    plan = ExecutionPlan(steps=(PlanStep(description=goal, intent=intent),))
    brain = Brain(
        planner=StaticPlanner([plan]),
        executor=TaskExecutor(handlers={intent: _StreamingHandler()}),
    )
    return create_app(
        MambaRuntime(brain=brain),
        settings_path=tmp_path / "settings.json",
        reminders_path=tmp_path / "reminders.json",
    )


def _turn(client, text: str) -> list[dict]:
    """Send one text turn and collect frames until the turn ends."""
    with client.websocket_connect("/live") as ws:
        ws.receive_json()  # status: connected
        ws.send_json({"type": "text", "text": text})
        frames: list[dict] = []
        for _ in range(40):
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("type") in ("turnComplete", "cancelled"):
                break
        return frames


def test_live_emits_deltas_in_order_then_the_complete_frame(tmp_path):
    frames = _turn(TestClient(_app_for("Explain the findings", "explain", tmp_path)),
                   "Explain the findings")

    deltas = [f["text"] for f in frames if f.get("type") == "delta"]
    assert deltas == ["part1", "part2"]

    model = [
        f["text"] for f in frames
        if f.get("type") == "transcription" and f.get("role") == "model"
    ]
    assert model == ["part1part2"]  # authoritative complete text still arrives
    assert frames[-1]["type"] == "turnComplete"


def test_live_deltas_are_coalesced_into_few_frames(tmp_path):
    """Part 15: many tiny chunks must not become many tiny frames."""

    class _ChattyHandler:
        def run(self, task_input, context) -> TaskOutput:
            for i in range(200):
                streaming.emit_token(".")
            return TaskOutput(content="." * 200, success=True)

    plan = ExecutionPlan(steps=(PlanStep(description="Chatter", intent="explain"),))
    brain = Brain(
        planner=StaticPlanner([plan]),
        executor=TaskExecutor(handlers={"explain": _ChattyHandler()}),
    )
    app = create_app(
        MambaRuntime(brain=brain),
        settings_path=tmp_path / "settings.json",
        reminders_path=tmp_path / "reminders.json",
    )
    frames = _turn(TestClient(app), "Chatter please")

    deltas = [f for f in frames if f.get("type") == "delta"]
    assert len(deltas) < 50  # coalesced, not one frame per chunk
    assert "".join(f["text"] for f in deltas) == "." * 200  # nothing lost


def test_live_progress_frames_carry_a_stage(tmp_path):
    frames = _turn(TestClient(_app_for("Explain the findings", "explain", tmp_path)),
                   "Explain the findings")

    progress = [f for f in frames if f.get("type") == "progress"]
    assert progress, "expected at least one milestone frame"
    # The existing "milestone" key stays for back-compat; "stage" is the named channel.
    assert all("stage" in f and "milestone" in f for f in progress)


def test_live_model_frame_is_authoritative_when_deltas_diverge(tmp_path):
    """Adjustment 2: deltas are provisional; the model frame carries the real result."""

    class _DivergentHandler:
        def run(self, task_input, context) -> TaskOutput:
            streaming.emit_token("provisional ")
            return TaskOutput(content="authoritative answer", success=True)

    plan = ExecutionPlan(steps=(PlanStep(description="Diverge", intent="explain"),))
    brain = Brain(
        planner=StaticPlanner([plan]),
        executor=TaskExecutor(handlers={"explain": _DivergentHandler()}),
    )
    app = create_app(
        MambaRuntime(brain=brain),
        settings_path=tmp_path / "settings.json",
        reminders_path=tmp_path / "reminders.json",
    )
    frames = _turn(TestClient(app), "Diverge now")

    deltas = "".join(f["text"] for f in frames if f.get("type") == "delta")
    model = [
        f["text"] for f in frames
        if f.get("type") == "transcription" and f.get("role") == "model"
    ]
    assert model == ["authoritative answer"]
    assert deltas != model[0]  # the UI must finalize from the model frame, not deltas


class _StubRuntime:
    """Runtime stand-in that returns a fixed ExecutionResult."""

    def __init__(self, result: ExecutionResult):
        self.result = result
        self.saw_stream_sink = False

    def run(self, request, *, on_progress=None, cancel_token=None, stream_sink=None):
        self.saw_stream_sink = callable(stream_sink)
        if stream_sink is not None:
            stream_sink("partial ")
        return self.result


def _client_for_stub(result: ExecutionResult):
    stub = _StubRuntime(result)
    app = create_app(
        stub,
        settings_path=Path("settings.json"),
        reminders_path=Path("reminders.json"),
    )
    return TestClient(app), stub


def test_live_cancelled_turn_emits_cancelled_not_complete():
    """A cancelled ExecutionResult can never be reported as a completed turn."""
    result = ExecutionResult(
        execution_id="exec-cancel",
        status=ResultStatus.CANCELLED,
        goal="Explain the findings",
        observations=(),
        output="Cancelled",
    )
    client, stub = _client_for_stub(result)
    frames = _turn(client, "Explain the findings")

    types = [f.get("type") for f in frames]
    assert "cancelled" in types
    assert "turnComplete" not in types
    assert not [
        f for f in frames
        if f.get("type") == "transcription" and f.get("role") == "model"
    ]
    assert stub.saw_stream_sink is True


def test_live_failed_turn_is_reported_as_failure_not_delta_success():
    result = ExecutionResult(
        execution_id="exec-fail",
        status=ResultStatus.FAILED,
        goal="Explain the findings",
        observations=(),
        error="provider stream failed mid-answer",
    )
    client, _stub = _client_for_stub(result)
    frames = _turn(client, "Explain the findings")

    types = [f.get("type") for f in frames]
    assert "turnComplete" in types  # the turn must still close
    model = [
        f for f in frames
        if f.get("type") == "transcription" and f.get("role") == "model"
    ]
    assert model and model[0]["text"] == "provider stream failed mid-answer"


def test_api_chat_json_shape_is_unchanged(tmp_path):
    """Adjustment 1: /api/chat stays non-streaming JSON (no SSE was added)."""
    client = TestClient(_app_for("Explain the findings", "explain", tmp_path))
    resp = client.post("/api/chat", json={"input": "Explain the findings"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert set(resp.json()) >= {
        "execution_id", "status", "output", "error", "awaiting_approval", "reason",
    }
