"""Contracts for voice interface components."""

from __future__ import annotations

from typing import Protocol


class STTProvider(Protocol):
    """Speech-to-text transcription provider."""

    def transcribe(self, audio_bytes: bytes, *, mime_type: str = "audio/wav") -> str:
        """Transcribe audio bytes to text."""
        ...


class TTSProvider(Protocol):
    """Text-to-speech audio synthesis provider."""

    def synthesize(self, text: str) -> bytes:
        """Synthesize text into audio bytes."""
        ...


class AudioCapture(Protocol):
    """Captures audio from microphone."""

    def record(self, duration_seconds: float | None = None) -> bytes:
        """Record audio and return WAV-encoded bytes."""
        ...


class AudioPlayer(Protocol):
    """Plays audio through speaker."""

    def play(self, audio_bytes: bytes, *, mime_type: str = "audio/mpeg") -> None:
        """Play audio bytes through local speaker."""
        ...

