"""Mamba Transport Adapter — FastAPI HTTP & WebSocket Gateway.

This module is strictly a transport adapter bridging HTTP and WebSocket
clients (browser, desktop UI) directly to MambaRuntime.

It contains ZERO application planning, ZERO routing decisions, and
ZERO tool execution logic. All intelligence remains in Mamba Core.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.runtime import MambaRuntime
from core.types import ExecutionResult, ResultStatus, UserRequest
from voice.errors import VoiceError

log = logging.getLogger("mamba.transport")

# Voice-turn audio limits: WAV-framed utterances only (16-bit PCM container
# required — the STT provider cannot parse headerless raw PCM).
_MAX_VOICE_AUDIO_BYTES = 10 * 1024 * 1024


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
    app = FastAPI(
        title="Mamba AI Transport Gateway",
        description="Lightweight HTTP/WebSocket transport adapter for Mamba Core.",
        version="1.0.0",
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
        result = runtime.run(user_req)
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
        prompt, result = voice_interface.process_voice_input(
            audio_bytes, mime_type=mime_type, speak_response=False
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

        try:
            while True:
                raw_msg = await websocket.receive_text()
                try:
                    data = json.loads(raw_msg)
                except Exception:
                    continue

                # 1. Video frame (screen capture) - acknowledged
                if data.get("type") == "video":
                    continue

                # Execute through canonical MambaRuntime with milestone updates
                def on_progress_sync(milestone: str) -> None:
                    try:
                        # Non-blocking best effort send
                        import asyncio
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(
                                websocket.send_json({"type": "progress", "milestone": milestone})
                            )
                    except Exception:
                        pass

                # 2. Incoming text or voice-turn audio.
                # Voice turns carry WAV-framed audio (NOT headerless raw PCM)
                # and are tagged input_modality="voice" so safety policy can
                # distinguish spoken input from typed input.
                prompt_text: str | None = None
                input_modality = "text"
                result: ExecutionResult | None = None
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
                    try:
                        prompt_text, result = voice_interface.process_voice_input(
                            audio_bytes,
                            mime_type="audio/wav",
                            speak_response=False,
                            on_progress=on_progress_sync,
                        )
                    except VoiceError as exc:
                        await websocket.send_json({"type": "error", "error": f"Voice processing failed: {exc}"})
                        continue
                    if not prompt_text:
                        await websocket.send_json({"type": "error", "error": "No speech detected."})
                        continue

                if not prompt_text:
                    continue

                # Send user transcription to UI
                await websocket.send_json({
                    "type": "transcription",
                    "role": "user",
                    "text": prompt_text,
                })
                await websocket.send_json({"type": "status", "status": "thinking"})

                if result is None:
                    # Text path: execute here. (Voice path already executed
                    # inside process_voice_input, tagged input_modality="voice".)
                    result = runtime.run(prompt_text, on_progress=on_progress_sync)
                formatted = _format_execution_response(result)

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

