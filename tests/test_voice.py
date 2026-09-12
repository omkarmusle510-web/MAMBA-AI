"""Focused unit and integration test suite for Mamba Voice Interface."""

from __future__ import annotations

import io
import wave
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.brain import Brain
from core.types import ExecutionPlan, ExecutionResult, Observation, PlanStep, ResultStatus, UserRequest
from voice.audio import MicrophoneCapture, SpeakerPlayer
from voice.errors import AudioCaptureError, STTError, TTSError, VoiceError
from voice.interface import VoiceInterface
from voice.stt import GroqSTTProvider
from voice.tts import CloudflareTTSProvider


def _make_dummy_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)
    return buf.getvalue()


class MockBrain:
    """Mock execution runner simulating Mamba Core."""

    def __init__(self, response_text: str = "This is Mamba speaking.") -> None:
        self.response_text = response_text
        self.last_request = None

    def run(self, request: str | UserRequest) -> ExecutionResult:
        self.last_request = request
        goal = request if isinstance(request, str) else request.goal
        return ExecutionResult(
            execution_id="test-exec-1",
            status=ResultStatus.COMPLETED,
            goal=goal,
            observations=(Observation(step_id="step-1", content=self.response_text, success=True),),
            output=self.response_text,
        )


class TestGroqSTTProvider:
    def test_missing_api_key_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(STTError, match="Groq API key is required"):
            GroqSTTProvider(api_key="")

    def test_empty_audio_raises_error(self) -> None:
        provider = GroqSTTProvider(api_key="test_key")
        with pytest.raises(STTError, match="audio data is empty"):
            provider.transcribe(b"")

    def test_mock_transcription(self) -> None:
        mock_post = MagicMock(return_value={"text": "Hello Mamba"})
        provider = GroqSTTProvider(api_key="test_key", _http_post=mock_post)
        result = provider.transcribe(_make_dummy_wav())
        assert result == "Hello Mamba"
        mock_post.assert_called_once()

    def test_sanitization_masks_key_in_errors(self) -> None:
        def bad_post(*args: Any, **kwargs: Any) -> Any:
            raise Exception("Request failed with secret_key_12345 in URL")

        provider = GroqSTTProvider(api_key="secret_key_12345", _http_post=bad_post)
        with pytest.raises(STTError) as exc_info:
            provider.transcribe(_make_dummy_wav())
        assert "secret_key_12345" not in str(exc_info.value)
        assert "***" in str(exc_info.value)


class TestCloudflareTTSProvider:
    def test_missing_credentials_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        with pytest.raises(TTSError, match="Account ID is required"):
            CloudflareTTSProvider(account_id="", api_token="tok")
        with pytest.raises(TTSError, match="API Token is required"):
            CloudflareTTSProvider(account_id="acc", api_token="")

    def test_empty_text_raises_error(self) -> None:
        provider = CloudflareTTSProvider(account_id="acc", api_token="tok")
        with pytest.raises(TTSError, match="text to synthesize is empty"):
            provider.synthesize("   ")

    def test_mock_synthesis(self) -> None:
        mock_post = MagicMock(return_value=b"fake_mp3_audio_stream")
        provider = CloudflareTTSProvider(account_id="acc", api_token="tok", _http_post=mock_post)
        audio = provider.synthesize("Hello")
        assert audio == b"fake_mp3_audio_stream"
        mock_post.assert_called_once()

    def test_sanitization_masks_secrets_in_errors(self) -> None:
        def bad_post(*args: Any, **kwargs: Any) -> Any:
            raise Exception("Failure in acc_secret_999 with tok_secret_888")

        provider = CloudflareTTSProvider(
            account_id="acc_secret_999", api_token="tok_secret_888", _http_post=bad_post
        )
        with pytest.raises(TTSError) as exc_info:
            provider.synthesize("Test")
        assert "acc_secret_999" not in str(exc_info.value)
        assert "tok_secret_888" not in str(exc_info.value)
        assert "***" in str(exc_info.value)


class TestVoiceInterface:
    def test_end_to_end_voice_turn(self) -> None:
        mock_brain = MockBrain("I can do that for you.")
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "Show system info"
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b"mp3_audio_bytes"
        mock_player = MagicMock()

        voice = VoiceInterface(
            brain=mock_brain,  # type: ignore
            stt=mock_stt,
            tts=mock_tts,
            player=mock_player,
        )

        dummy_audio = _make_dummy_wav()
        prompt, result = voice.process_voice_input(dummy_audio)

        assert prompt == "Show system info"
        assert mock_brain.last_request == "Show system info"
        assert result.status == ResultStatus.COMPLETED
        mock_stt.transcribe.assert_called_once_with(dummy_audio, mime_type="audio/wav")
        mock_tts.synthesize.assert_called_once_with("I can do that for you.")
        mock_player.play.assert_called_once_with(b"mp3_audio_bytes")

    def test_empty_speech_handled_cleanly(self) -> None:
        mock_brain = MockBrain()
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "   "
        mock_tts = MagicMock()

        voice = VoiceInterface(brain=mock_brain, stt=mock_stt, tts=mock_tts)  # type: ignore
        prompt, result = voice.process_voice_input(_make_dummy_wav())

        assert prompt == ""
        assert result.status == ResultStatus.FAILED
        assert "no speech detected" in result.error
        mock_tts.synthesize.assert_not_called()

    def test_tts_failure_degrades_gracefully_without_failing_core_result(self) -> None:
        mock_brain = MockBrain("Output text")
        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = "Run command"
        mock_tts = MagicMock()
        mock_tts.synthesize.side_effect = TTSError("Cloudflare rate limited")
        mock_player = MagicMock()

        voice = VoiceInterface(
            brain=mock_brain,  # type: ignore
            stt=mock_stt,
            tts=mock_tts,
            player=mock_player,
        )

        # Must NOT raise exception despite TTS failure
        prompt, result = voice.process_voice_input(_make_dummy_wav())
        assert prompt == "Run command"
        assert result.status == ResultStatus.COMPLETED
        assert result.output == "Output text"
        mock_player.play.assert_not_called()

