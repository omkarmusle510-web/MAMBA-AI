"""Mamba Voice subsystem."""

from .audio import MicrophoneCapture, SpeakerPlayer
from .errors import (
    AudioCaptureError,
    AudioPlaybackError,
    STTError,
    TTSError,
    VoiceError,
)
from .interface import VoiceInterface
from .normalization import normalize_speech_text
from .protocols import AudioCapture, AudioPlayer, STTProvider, TTSProvider
from .stt import GroqSTTProvider
from .tts import CloudflareTTSProvider

__all__ = [
    "AudioCapture",
    "AudioCaptureError",
    "AudioPlaybackError",
    "AudioPlayer",
    "CloudflareTTSProvider",
    "GroqSTTProvider",
    "MicrophoneCapture",
    "STTError",
    "STTProvider",
    "SpeakerPlayer",
    "TTSError",
    "TTSProvider",
    "VoiceError",
    "VoiceInterface",
    "normalize_speech_text",
]

