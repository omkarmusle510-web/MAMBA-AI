"""Voice interface coordinator for Mamba AI."""

from __future__ import annotations

import sys
from typing import Any

from core.brain import Brain
from core.types import ExecutionResult, ResultStatus, UserRequest

from .audio import MicrophoneCapture, SpeakerPlayer
from .errors import AudioCaptureError, AudioPlaybackError, STTError, TTSError, VoiceError
from .normalization import normalize_speech_text
from .protocols import AudioCapture, AudioPlayer, STTProvider, TTSProvider
from .stt import GroqSTTProvider
from .tts import CloudflareTTSProvider


class VoiceInterface:
    """Coordinates voice interaction through the standard Mamba Core pipeline.

    Flow:
        Microphone -> STT (Groq) -> Mamba Core -> TTS (Cloudflare) -> Speaker
    """

    def __init__(
        self,
        brain: Brain,
        *,
        stt: STTProvider | None = None,
        tts: TTSProvider | None = None,
        capture: AudioCapture | None = None,
        player: AudioPlayer | None = None,
    ) -> None:
        self._brain = brain
        self._stt = stt or GroqSTTProvider()
        self._tts = tts or CloudflareTTSProvider()
        self._capture = capture or MicrophoneCapture()
        self._player = player or SpeakerPlayer()
        self._tts_degraded = False

    @property
    def brain(self) -> Brain:
        return self._brain

    @property
    def stt(self) -> STTProvider:
        return self._stt

    @property
    def tts(self) -> TTSProvider:
        return self._tts

    @property
    def tts_degraded(self) -> bool:
        return self._tts_degraded

    def process_voice_input(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/wav",
        speak_response: bool = True,
    ) -> tuple[str, ExecutionResult]:
        """Process pre-recorded or captured audio through Mamba Core."""
        if not audio_bytes:
            raise AudioCaptureError("no audio captured from microphone")

        # 1. Transcribe
        try:
            transcript = self._stt.transcribe(audio_bytes, mime_type=mime_type)
        except Exception as exc:
            raise STTError(f"speech transcription failed: {exc}") from exc

        clean_transcript = transcript.strip()
        if not clean_transcript:
            return "", ExecutionResult(
                execution_id="",
                status=ResultStatus.FAILED,
                goal="",
                observations=(),
                error="no speech detected in audio input",
            )

        print(f"\nmamba (voice)> {clean_transcript}")

        # 2. Execute through existing Mamba Core
        result = self._brain.run(clean_transcript)

        # 3. Format response for user
        text_to_speak = self._extract_speech_text(result)

        # 4. Synthesize and play response audio (with clean error fallback)
        if speak_response and text_to_speak:
            self._speak(text_to_speak)

        return clean_transcript, result

    def record_and_process(
        self,
        *,
        duration_seconds: float | None = None,
        interactive: bool = True,
        speak_response: bool = True,
    ) -> tuple[str, ExecutionResult] | None:
        """Capture from microphone and run through Mamba."""
        try:
            if interactive and hasattr(self._capture, "record_interactive"):
                audio_bytes = self._capture.record_interactive()
            else:
                audio_bytes = self._capture.record(duration_seconds)
        except Exception as exc:
            print(f"\n[Microphone Error: {exc}]", file=sys.stderr)
            return None

        if not audio_bytes:
            print("\n[No audio recorded]")
            return None

        try:
            return self.process_voice_input(audio_bytes, speak_response=speak_response)
        except VoiceError as exc:
            print(f"\n[Voice Error: {exc}]", file=sys.stderr)
            return None
        except Exception as exc:
            print(f"\n[Execution Error: {exc}]", file=sys.stderr)
            return None

    def voice_loop(self) -> None:
        """Run interactive voice conversation loop."""
        print("=" * 60)
        print("Mamba Voice Interface")
        print("Press [Enter] to record your command.")
        print("Type 'text' to switch to text mode, or 'exit' to quit.")
        print("=" * 60)

        while True:
            try:
                action = input("\n[Press Enter to Speak | 'exit' to quit]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting voice interface.")
                break

            if action in ("exit", "quit", "q"):
                print("Exiting voice interface.")
                break
            if action in ("text", "cli"):
                print("Switching to text mode.")
                break

            result_pair = self.record_and_process(interactive=True)
            if result_pair is not None:
                _, exec_result = result_pair
                self._display_result(exec_result)

    def _speak(self, text: str) -> None:
        """Synthesize text and play through speaker, absorbing failures."""
        if self._tts_degraded:
            return

        try:
            # Normalize Markdown formatting into natural, fluent spoken text
            spoken_text = normalize_speech_text(text)
            if not spoken_text:
                return

            if len(spoken_text) > 800:
                # Truncate overly long outputs for TTS to prevent excessive audio duration
                spoken_text = spoken_text[:800] + "... and more."

            audio = self._tts.synthesize(spoken_text)
            self._player.play(audio)
        except Exception as exc:
            # Audio playback failure must never fail or crash Mamba Core
            err_msg = str(exc)
            if "429" in err_msg or "quota" in err_msg.lower() or "neurons" in err_msg.lower():
                self._tts_degraded = True
                print(
                    "\n[Voice TTS Notice: Cloudflare TTS quota reached (HTTP 429). Continuing session in text-only audio mode.]",
                    file=sys.stderr,
                )
            else:
                print(f"[Speech Synthesis / Playback Warning: {exc}]", file=sys.stderr)

    def _extract_speech_text(self, result: ExecutionResult) -> str:
        """Extract user-facing text from execution result for speech synthesis."""
        if result.status == ResultStatus.COMPLETED:
            if result.output:
                return result.output.strip()
            if result.observations:
                return " ".join(obs.content.strip() for obs in result.observations)
            return "Task completed successfully."
        else:
            if result.output and ("requires user confirmation" in result.output.lower() or "requires user approval" in result.output.lower()):
                return result.output.strip()
            if result.error:
                if "requires user confirmation" in result.error.lower() or "requires user approval" in result.error.lower():
                    return result.error.strip()
                return f"Task failed: {result.error}"
            if result.output:
                return result.output.strip()
            return "Task failed."

    def _display_result(self, result: ExecutionResult) -> None:
        """Print result to stdout matching app.py display conventions."""
        print(f"\n[Status: {result.status.value}]")
        if result.status == ResultStatus.COMPLETED:
            if result.output:
                print(result.output.strip())
            elif result.observations:
                for obs in result.observations:
                    print(obs.content.strip())
        else:
            if result.output and ("requires user confirmation" in result.output.lower() or "requires user approval" in result.output.lower()):
                print(result.output.strip())
            elif result.error:
                print(f"Error: {result.error}")
            elif result.output:
                print(result.output.strip())
        print()

