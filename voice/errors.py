"""Voice subsystem exception hierarchy."""

from __future__ import annotations


class VoiceError(Exception):
    """Base error for voice interface failures."""


class AudioCaptureError(VoiceError):
    """Raised when capturing audio from microphone fails."""


class AudioPlaybackError(VoiceError):
    """Raised when audio playback fails."""


class STTError(VoiceError):
    """Raised when speech-to-text transcription fails."""


class TTSError(VoiceError):
    """Raised when text-to-speech synthesis fails."""

