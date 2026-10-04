"""Mamba Transport Adapter — FastAPI HTTP & WebSocket Gateway.

This module is strictly a transport adapter bridging HTTP and WebSocket
clients (browser, desktop UI) directly to MambaRuntime.

It contains ZERO application planning, ZERO routing decisions, and
ZERO tool execution logic. All intelligence remains in Mamba Core.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.cancellation import CancellationToken, reset_current_token, set_current_token
from core.runtime import MambaRuntime
from core.types import ExecutionResult, ResultStatus, UserRequest
from voice.errors import VoiceError

log = logging.getLogger("mamba.transport")

# Voice-turn audio limits: WAV-framed utterances only (16-bit PCM container
# required — the STT provider cannot parse headerless raw PCM).
_MAX_VOICE_AUDIO_BYTES = 10 * 1024 * 1024

# Sentinel the /live receiver enqueues when the socket closes, so the turn loop
# can distinguish "client gone" from an ordinary inbound message.
_DISCONNECT = object()

# Streamed deltas are coalesced so a 200-chunk answer does not become 200
# WebSocket frames (Spec Part 15). The first delta always flushes immediately;
# later ones accumulate for this window. Tunable for diagnostics only.
_DELTA_COALESCE_SECONDS = max(
    0.0, float(os.environ.get("MAMBA_STREAM_COALESCE_MS", "40")) / 1000.0
)


def _looks_like_wav(data: bytes) -> bool:
    """Minimal RIFF/WAVE header check for inbound voice audio."""
    return len(data) >= 44 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE"


class ChatRequest(BaseModel):
    input: str
    metadata: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    execution_id: str
    status: str
    output: str | None = None
    error: str | None = None
    awaiting_approval: bool = False
    reason: str | None = None


def _format_execution_response(result: ExecutionResult) -> ChatResponse:
    """Extract clean response data and approval flag from ExecutionResult."""
    is_approval = any(
        obs.metadata.get("awaiting_approval") for obs in result.observations
    )
    reason = next(
        (str(obs.metadata.get("reason")) for obs in result.observations if obs.metadata.get("reason")),
        None,
    )
    return ChatResponse(
        execution_id=result.execution_id,
        status=result.status.value,
        output=result.output,
        error=result.error,
        awaiting_approval=is_approval,
        reason=reason,
    )


def create_app(
    runtime: MambaRuntime,
    voice_interface: Any | None = None,
    settings_path: Path | None = None,
    reminders_path: Path | None = None,
) -> FastAPI:
    """Create FastAPI transport adapter application around MambaRuntime."""

    # A single dedicated worker serialises Brain.run off the event loop. One
    # worker is deliberate: Mamba's Brain, browser session and SQLite memory
    # store are single-execution state, so serialising preserves the existing
    # de-facto behaviour while freeing the loop to serve health checks, sockets
    # and cancellations concurrently.
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mamba-worker")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            # Application shutdown is separate from per-request cancellation:
            # drop queued work without blocking, then let the runtime perform
            # its own bounded teardown (stops MCP/browser, closes memory).
            worker.shutdown(wait=False, cancel_futures=True)
            try:
                runtime.shutdown()
            except Exception:
                log.warning("runtime shutdown raised during teardown", exc_info=True)

    app = FastAPI(
        title="Mamba AI Transport Gateway",
        description="Lightweight HTTP/WebSocket transport adapter for Mamba Core.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    resolved_settings_path = settings_path or (Path.cwd() / ".mamba" / "settings.json")
    resolved_reminders_path = reminders_path or (Path.cwd() / ".mamba" / "reminders.json")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "mamba"}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat_endpoint(req: ChatRequest) -> ChatResponse:
        """Forward user prompt to MambaRuntime and return execution outcome."""
        prompt = req.input.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="input must not be empty")

        user_req = UserRequest(goal=prompt, metadata=dict(req.metadata or {}))
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(worker, functools.partial(runtime.run, user_req))
        return _format_execution_response(result)

    @app.post("/api/voice")
    async def voice_endpoint(file: UploadFile = File(...)) -> dict[str, Any]:
        """Forward voice audio to Mamba VoiceInterface for transcription and execution."""
        if voice_interface is None:
            raise HTTPException(
                status_code=503,
                detail="VoiceInterface is not configured on this server.",
            )

        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Empty audio payload")

        mime_type = file.content_type or "audio/wav"
        loop = asyncio.get_running_loop()
        prompt, result = await loop.run_in_executor(
            worker,
            functools.partial(
                voice_interface.process_voice_input,
                audio_bytes,
                mime_type=mime_type,
                speak_response=False,
            ),
        )

        response_data = _format_execution_response(result)
        return {
            "prompt": prompt,
            "result": response_data.model_dump(),
        }

    @app.websocket("/live")
    async def websocket_live_endpoint(websocket: WebSocket) -> None:
        """Real-time bidirectional WebSocket transport for Mamba UI."""
        await websocket.accept()
        await websocket.send_json({"type": "status", "status": "connected"})

        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue[Any] = asyncio.Queue()

        async def _receiver() -> None:
            """Sole reader of the socket, feeding inbound frames to ``inbox``.

            Receiving on one dedicated task (never cancelled mid-frame) lets the
            turn loop watch for a cancel message while a turn executes without
            racing or dropping socket reads.
            """
            try:
                while True:
                    raw = await websocket.receive_text()
                    await inbox.put(raw)
            except WebSocketDisconnect:
                await inbox.put(_DISCONNECT)
            except Exception:
                await inbox.put(_DISCONNECT)

        recv_task = asyncio.create_task(_receiver())

        def make_progress():
            """Build a worker-thread-safe progress callback.

            Milestones are emitted on the single worker thread; they are
            marshalled back onto the event loop with ``run_coroutine_threadsafe``
            and fire-and-forget so the worker never blocks on socket I/O.

            ``stage`` names the execution phase (Spec Part 6: progress is a
            separate channel from text deltas); ``milestone`` is kept for the
            existing clients.
            """

            def on_progress(milestone: str) -> None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        websocket.send_json({
                            "type": "progress",
                            "milestone": milestone,
                            "stage": milestone,
                        }),
                        loop,
                    )
                except Exception:
                    pass

            return on_progress

        def make_stream_sink():
            """Build a worker-thread-safe sink that coalesces provisional deltas.

            Deltas are incremental UI text only. The authoritative frame is the
            ``transcription``/``role=model`` frame the turn handler sends once
            :meth:`MambaRuntime.run` returns its ``ExecutionResult``.
            """
            buf: list[str] = []
            last_flush = [0.0]

            def _send(batch: str):
                try:
                    return asyncio.run_coroutine_threadsafe(
                        websocket.send_json({"type": "delta", "text": batch}),
                        loop,
                    )
                except Exception:
                    return None

            def flush():
                """Drain the buffer; returns the send future so the caller can
                await it before the authoritative frame (frame order)."""
                if not buf:
                    return None
                batch = "".join(buf)
                buf.clear()
                last_flush[0] = time.monotonic()
                return _send(batch)

            def on_delta(text: str) -> None:
                buf.append(text)
                if time.monotonic() - last_flush[0] >= _DELTA_COALESCE_SECONDS:
                    flush()

            on_delta.flush = flush  # type: ignore[attr-defined]
            return on_delta

        async def _run_cancellable(fn: Any) -> Any:
            """Offload ``fn(token)`` to the worker and await it, honouring cancel.

            While the turn runs, inbound ``{"type": "cancel"}`` frames signal the
            cooperative CancellationToken. Any other frame is put back on the
            inbox untouched so the next turn is processed in order after this one.
            A client disconnect cancels the turn and unwinds the handler.
            """
            token = CancellationToken()
            exec_fut = loop.run_in_executor(worker, fn, token)
            while not exec_fut.done():
                get_fut = asyncio.ensure_future(inbox.get())
                done, _ = await asyncio.wait(
                    {exec_fut, get_fut}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_fut not in done:
                    get_fut.cancel()
                    with suppress(asyncio.CancelledError):
                        await get_fut
                    continue
                raw = get_fut.result()
                if raw is _DISCONNECT:
                    token.cancel()
                    with suppress(Exception):
                        await exec_fut
                    raise WebSocketDisconnect(code=1000)
                is_cancel = False
                try:
                    msg = json.loads(raw)
                    is_cancel = isinstance(msg, dict) and msg.get("type") == "cancel"
                except Exception:
                    is_cancel = False
                if is_cancel:
                    token.cancel()
                    await websocket.send_json({"type": "status", "status": "cancelling"})
                else:
                    await inbox.put(raw)
                    break
            return await exec_fut

        try:
            while True:
                raw_msg = await inbox.get()
                if raw_msg is _DISCONNECT:
                    raise WebSocketDisconnect(code=1000)
                try:
                    data = json.loads(raw_msg)
                except Exception:
                    continue

                # 1. Video frame (screen capture) - acknowledged
                if data.get("type") == "video":
                    continue

                # A cancel with no turn in flight is a no-op.
                if data.get("type") == "cancel":
                    continue

                # 2. Incoming text or voice-turn audio.
                # Voice turns carry WAV-framed audio (NOT headerless raw PCM)
                # and are tagged input_modality="voice" so safety policy can
                # distinguish spoken input from typed input.
                prompt_text: str | None = None
                input_modality = "text"
                result: ExecutionResult | None = None
                audio_bytes: bytes | None = None
                if data.get("type") == "text":
                    prompt_text = str(data.get("text", "")).strip()
                elif data.get("type") == "audio" and data.get("audio"):
                    input_modality = "voice"
                    if voice_interface is None:
                        await websocket.send_json({"type": "error", "error": "Voice is not configured on server."})
                        continue
                    try:
                        audio_bytes = base64.b64decode(data["audio"])
                    except Exception:
                        await websocket.send_json({"type": "error", "error": "Invalid audio payload."})
                        continue
                    if len(audio_bytes) > _MAX_VOICE_AUDIO_BYTES:
                        await websocket.send_json({"type": "error", "error": "Audio payload too large."})
                        continue
                    if not _looks_like_wav(audio_bytes):
                        await websocket.send_json({"type": "error", "error": "Audio must be WAV-framed (16-bit PCM)."})
                        continue

                on_progress_sync = make_progress()
                stream_sink: Any = None

                if input_modality == "voice":
                    # Voice path: transcription + execution happen together off
                    # the loop. The ambient token is bound on the worker so the
                    # nested Brain.run observes cancellation.
                    await websocket.send_json({"type": "status", "status": "thinking"})

                    def _voice_turn(token: CancellationToken, _audio: bytes = audio_bytes, _prog=on_progress_sync):
                        reset = set_current_token(token)
                        try:
                            return voice_interface.process_voice_input(
                                _audio,
                                mime_type="audio/wav",
                                speak_response=False,
                                on_progress=_prog,
                            )
                        finally:
                            reset_current_token(reset)

                    try:
                        prompt_text, result = await _run_cancellable(_voice_turn)
                    except VoiceError as exc:
                        await websocket.send_json({"type": "error", "error": f"Voice processing failed: {exc}"})
                        continue
                    if not prompt_text:
                        await websocket.send_json({"type": "error", "error": "No speech detected."})
                        continue
                    await websocket.send_json({
                        "type": "transcription",
                        "role": "user",
                        "text": prompt_text,
                    })
                else:
                    if not prompt_text:
                        continue
                    # Send user transcription to UI
                    await websocket.send_json({
                        "type": "transcription",
                        "role": "user",
                        "text": prompt_text,
                    })
                    await websocket.send_json({"type": "status", "status": "thinking"})

                    # Text turns stream provisional deltas. The voice branch
                    # deliberately does not: it keeps returning one complete
                    # transcription + one audio blob (Spec Part 11, Phase 5
                    # owns voice).
                    stream_sink = make_stream_sink()

                    def _text_turn(token: CancellationToken, _prompt: str = prompt_text, _prog=on_progress_sync, _sink=stream_sink):
                        return runtime.run(
                            _prompt, on_progress=_prog, cancel_token=token, stream_sink=_sink,
                        )

                    result = await _run_cancellable(_text_turn)

                formatted = _format_execution_response(result)

                # Cancellation is terminal: the turn is reported as cancelled
                # and can never be presented as a completed answer. Buffered
                # provisional deltas are dropped rather than finalized.
                if result is not None and result.status == ResultStatus.CANCELLED:
                    await websocket.send_json({"type": "cancelled"})
                    await websocket.send_json({"type": "status", "status": "listening"})
                    continue

                # Drain coalesced deltas before the authoritative frame so the
                # UI always sees deltas → complete in that order.
                if stream_sink is not None:
                    _flush = getattr(stream_sink, "flush", None)
                    pending = _flush() if callable(_flush) else None
                    if pending is not None:
                        with suppress(Exception):
                            await asyncio.wrap_future(pending)

                # Voice turns: speak the outcome back over the socket as a
                # complete audio blob (the TTS provider returns whole audio,
                # not a stream — no fake streaming). None-safe: falls back
                # to text-only when TTS is degraded or unconfigured.
                voice_audio_msg: dict[str, Any] | None = None
                if input_modality == "voice" and voice_interface is not None:
                    speak_text = (
                        "I need your confirmation on screen to proceed."
                        if formatted.awaiting_approval
                        else (formatted.output or formatted.error or "Done.")
                    )
                    tts_out = voice_interface.synthesize_speech_text(speak_text)
                    if tts_out is not None:
                        tts_format, tts_bytes = tts_out
                        voice_audio_msg = {
                            "type": "audio",
                            "format": tts_format,
                            "audio": base64.b64encode(tts_bytes).decode("ascii"),
                        }

                if formatted.awaiting_approval:
                    await websocket.send_json({"type": "status", "status": "permission"})
                    await websocket.send_json({
                        "type": "permission_request",
                        "command": formatted.reason or prompt_text,
                        "reason": formatted.reason or "Command requires user confirmation",
                    })
                    if voice_audio_msg is not None:
                        await websocket.send_json(voice_audio_msg)
                    # Voice turn ends here: HIGH-risk actions require visual
                    # confirmation (SudoPopup click / typed approval).
                    await websocket.send_json({"type": "turnComplete"})
                else:
                    response_text = formatted.output or formatted.error or "Done."
                    await websocket.send_json({
                        "type": "transcription",
                        "role": "model",
                        "text": response_text,
                    })
                    if voice_audio_msg is not None:
                        await websocket.send_json(voice_audio_msg)
                    await websocket.send_json({"type": "turnComplete"})
                    await websocket.send_json({"type": "status", "status": "listening"})

        except WebSocketDisconnect:
            log.info("Client disconnected from /live WebSocket.")
        except Exception as exc:
            log.warning("WebSocket error in /live transport: %s", exc)
        finally:
            recv_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await recv_task

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        """Read user preferences from .mamba/settings.json."""
        if resolved_settings_path.exists():
            try:
                return json.loads(resolved_settings_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    @app.post("/api/settings")
    async def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
        """Write user preferences to .mamba/settings.json."""
        resolved_settings_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return {"status": "ok"}

    @app.get("/api/reminders")
    async def get_reminders() -> list[dict[str, Any]]:
        """Read scheduled reminders from .mamba/reminders.json."""
        if resolved_reminders_path.exists():
            try:
                return json.loads(resolved_reminders_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    @app.post("/api/reminders")
    async def save_reminders(reminders: list[dict[str, Any]]) -> dict[str, Any]:
        """Save scheduled reminders to .mamba/reminders.json."""
        resolved_reminders_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_reminders_path.write_text(json.dumps(reminders, indent=2), encoding="utf-8")
        return {"status": "ok"}

    return app

