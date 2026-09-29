"""Focused tests for Mamba Transport Adapter (FastAPI HTTP gateway)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.server import create_app
from core.brain import Brain
from core.context import ExecutionContext
from core.runtime import MambaRuntime
from core.types import ExecutionPlan, PlanStep, ResultStatus
from permissions.policy import DefaultPermissionPolicy
from permissions.types import RiskLevel
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from tests.test_core_lifecycle import EchoTaskHandler, StaticPlanner


def test_chat_endpoint_delegates_to_runtime(tmp_path: Path):
    """Verify HTTP POST /api/chat delegates directly to MambaRuntime.run() and returns result."""
    step = PlanStep(description="Say hello", intent="greet")
    plan = ExecutionPlan(steps=(step,))
    planner = StaticPlanner([plan])

    handler = EchoTaskHandler()
    executor = TaskExecutor(handlers={"greet": handler})
    brain = Brain(planner=planner, executor=executor)
    runtime = MambaRuntime(brain=brain)

    app = create_app(runtime, settings_path=tmp_path / "settings.json", reminders_path=tmp_path / "reminders.json")
    client = TestClient(app)

    response = client.post("/api/chat", json={"input": "Say hello"})
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "completed"
    assert "Executed greet: Say hello" in data["output"]
    assert data["awaiting_approval"] is False


def test_permission_ask_flow(tmp_path: Path):
    """Verify that a high-risk action yields awaiting_approval=True, and sending 'yes' resumes."""
    # Step 1: Destructive action without approved flag (yields ASK)
    step1 = PlanStep(
        description="Delete database",
        intent="delete_file",
        metadata={"path": "production.db", "risk_level": RiskLevel.HIGH},
    )
    plan1 = ExecutionPlan(steps=(step1,))

    # Handler records executions
    executed: list[str] = []

    class MockDeleteHandler:
        def run(self, task_in: TaskInput, ctx: ExecutionContext) -> TaskOutput:
            executed.append(task_in.intent)
            return TaskOutput(content="Database deleted", success=True)

    executor = TaskExecutor(handlers={"delete_file": MockDeleteHandler()})
    planner = StaticPlanner([plan1])
    brain = Brain(
        planner=planner,
        executor=executor,
        permissions=DefaultPermissionPolicy(),
    )
    runtime = MambaRuntime(brain=brain)

    app = create_app(runtime, settings_path=tmp_path / "settings.json", reminders_path=tmp_path / "reminders.json")
    client = TestClient(app)

    # 1. Trigger action requiring permission
    res1 = client.post("/api/chat", json={"input": "Delete production database"})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["awaiting_approval"] is True
    assert "user confirmation" in data1["output"].lower()
    assert len(executed) == 0  # Action not executed yet

    # 2. Approve action by sending "yes"
    res2 = client.post("/api/chat", json={"input": "yes"})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "completed"
    assert data2["awaiting_approval"] is False
    assert len(executed) == 1


def test_voice_approval_requires_visual_confirmation(tmp_path: Path):
    """A spoken 'yes' (input_modality=voice) must NOT approve a pending HIGH-risk action.

    Conservative voice-approval policy: pending approvals can only be resolved
    by typed/visual confirmation (SudoPopup click). The pending approval must
    survive the rejected voice approval.
    """
    step1 = PlanStep(
        description="Delete database",
        intent="delete_file",
        metadata={"path": "production.db", "risk_level": RiskLevel.HIGH},
    )
    plan1 = ExecutionPlan(steps=(step1,))
    executed: list[str] = []

    class MockDeleteHandler:
        def run(self, task_in: TaskInput, ctx: ExecutionContext) -> TaskOutput:
            executed.append(task_in.intent)
            return TaskOutput(content="Database deleted", success=True)

    executor = TaskExecutor(handlers={"delete_file": MockDeleteHandler()})
    planner = StaticPlanner([plan1])
    brain = Brain(planner=planner, executor=executor, permissions=DefaultPermissionPolicy())
    runtime = MambaRuntime(brain=brain)

    app = create_app(runtime, settings_path=tmp_path / "settings.json", reminders_path=tmp_path / "reminders.json")
    client = TestClient(app)

    # 1. Trigger action requiring permission
    res1 = client.post("/api/chat", json={"input": "Delete production database"})
    assert res1.status_code == 200
    assert res1.json()["awaiting_approval"] is True
    assert len(executed) == 0

    # 2. Voice-originated "yes" must NOT approve; pending approval survives.
    res2 = client.post("/api/chat", json={"input": "yes", "metadata": {"input_modality": "voice"}})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["awaiting_approval"] is False  # transport-level flag, not pending state
    assert "on screen" in data2["output"]
    assert len(executed) == 0

    # 3. Typed/visual "yes" still approves normally.
    res3 = client.post("/api/chat", json={"input": "yes"})
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["status"] == "completed"
    assert data3["awaiting_approval"] is False
    assert len(executed) == 1


def _make_wav_bytes() -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)
    return buf.getvalue()


def test_live_audio_branch_one_turn(tmp_path: Path):
    """Full /live voice-turn protocol: WAV in -> transcriptions -> TTS audio -> turnComplete."""
    import base64

    from core.types import ExecutionResult

    step = PlanStep(description="Answer question", intent="greet")
    plan = ExecutionPlan(steps=(step,))
    planner = StaticPlanner([plan])
    brain = Brain(planner=planner, executor=TaskExecutor(handlers={"greet": EchoTaskHandler()}))
    runtime = MambaRuntime(brain=brain)

    completed_result = ExecutionResult(
        execution_id="voice-exec-1",
        status=ResultStatus.COMPLETED,
        goal="What is the date?",
        observations=(),
        output="It is Tuesday.",
    )
    voice_interface = MagicMock()
    voice_interface.process_voice_input.return_value = ("What is the date?", completed_result)
    voice_interface.synthesize_speech_text.return_value = ("mp3", b"ID3" + b"\x00" * 64)

    app = create_app(
        runtime,
        voice_interface=voice_interface,
        settings_path=tmp_path / "settings.json",
        reminders_path=tmp_path / "reminders.json",
    )
    client = TestClient(app)

    wav_b64 = base64.b64encode(_make_wav_bytes()).decode("ascii")
    with client.websocket_connect("/live") as ws:
        assert ws.receive_json()["type"] == "status"  # connected

        ws.send_json({"type": "audio", "format": "wav", "audio": wav_b64})

        seen: list[dict] = []
        for _ in range(10):
            msg = ws.receive_json()
            seen.append(msg)
            if msg.get("type") == "turnComplete":
                break

        types = [m.get("type") for m in seen]
        assert "transcription" in types
        user_tx = [m for m in seen if m.get("type") == "transcription" and m.get("role") == "user"]
        assert user_tx and user_tx[0]["text"] == "What is the date?"
        model_tx = [m for m in seen if m.get("type") == "transcription" and m.get("role") == "model"]
        assert model_tx and model_tx[0]["text"] == "It is Tuesday."
        audio_msgs = [m for m in seen if m.get("type") == "audio"]
        assert len(audio_msgs) == 1
        assert audio_msgs[0]["format"] == "mp3"
        assert base64.b64decode(audio_msgs[0]["audio"]) == b"ID3" + b"\x00" * 64

    # Voice path tags modality and skips local speaker playback.
    voice_interface.process_voice_input.assert_called_once()
    kwargs = voice_interface.process_voice_input.call_args.kwargs
    assert kwargs["mime_type"] == "audio/wav"
    assert kwargs["speak_response"] is False


def test_live_audio_branch_rejects_non_wav(tmp_path: Path):
    """Headerless raw PCM must be rejected with an error, not silently mis-transcribed."""
    import base64

    step = PlanStep(description="noop", intent="greet")
    planner = StaticPlanner([ExecutionPlan(steps=(step,))])
    brain = Brain(planner=planner, executor=TaskExecutor(handlers={"greet": EchoTaskHandler()}))
    runtime = MambaRuntime(brain=brain)

    voice_interface = MagicMock()
    app = create_app(
        runtime,
        voice_interface=voice_interface,
        settings_path=tmp_path / "settings.json",
        reminders_path=tmp_path / "reminders.json",
    )
    client = TestClient(app)

    raw_b64 = base64.b64encode(b"\x00" * 3200).decode("ascii")  # no RIFF/WAVE header
    with client.websocket_connect("/live") as ws:
        assert ws.receive_json()["type"] == "status"
        ws.send_json({"type": "audio", "format": "pcm", "audio": raw_b64})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "WAV" in msg["error"]

    voice_interface.process_voice_input.assert_not_called()
