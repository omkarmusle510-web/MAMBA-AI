"""Audio capture and playback implementations."""

from __future__ import annotations

import ctypes
import io
import os
import tempfile
import time
import wave
from typing import Any

from .errors import AudioCaptureError, AudioPlaybackError

_DEFAULT_SAMPLE_RATE = 16000
_DEFAULT_CHANNELS = 1


class MicrophoneCapture:
    """Captures microphone audio using sounddevice and encodes to WAV."""

    def __init__(
        self,
        *,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        channels: int = _DEFAULT_CHANNELS,
        _sd_module: Any = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels

        if _sd_module is not None:
            self._sd = _sd_module
        else:
            try:
                import sounddevice as sd

                self._sd = sd
            except ImportError as exc:
                raise AudioCaptureError(
                    "sounddevice is not installed: pip install sounddevice"
                ) from exc

    def record(self, duration_seconds: float | None = 4.0) -> bytes:
        """Record audio for a fixed duration and return WAV bytes."""
        seconds = duration_seconds or 4.0
        num_frames = int(seconds * self._sample_rate)

        try:
            # Record float32 array [-1.0, 1.0]
            audio_data = self._sd.rec(
                frames=num_frames,
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                blocking=True,
            )
        except Exception as exc:
            raise AudioCaptureError(f"failed to record audio from microphone: {exc}") from exc

        return self._to_wav_bytes(audio_data)

    def record_interactive(self) -> bytes:
        """Record audio until user presses Enter and return WAV bytes."""
        import threading
        import numpy as np

        frames: list[np.ndarray] = []
        recording = True

        def callback(indata: np.ndarray, frame_count: int, time_info: Any, status: Any) -> None:
            if recording:
                frames.append(indata.copy())

        try:
            stream = self._sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                callback=callback,
            )
            with stream:
                input("Recording... Press [Enter] to finish speaking.")
                recording = False
        except Exception as exc:
            raise AudioCaptureError(f"failed during interactive microphone recording: {exc}") from exc

        if not frames:
            return b""

        full_audio = np.concatenate(frames, axis=0)
        return self._to_wav_bytes(full_audio)

    def _to_wav_bytes(self, int16_data: Any) -> bytes:
        """Pack int16 numpy array into standard WAV bytes."""
        wav_buf = io.BytesIO()
        try:
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(self._channels)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(self._sample_rate)
                wf.writeframes(int16_data.tobytes())
            return wav_buf.getvalue()
        except Exception as exc:
            raise AudioCaptureError(f"failed to encode WAV audio: {exc}") from exc


class SpeakerPlayer:
    """Plays audio through speaker using Windows MCI or platform player."""

    def __init__(self, *, _play_fn: Any = None) -> None:
        self._play_fn = _play_fn

    def play(self, audio_bytes: bytes, *, mime_type: str = "audio/mpeg") -> None:
        """Play audio bytes through local speaker."""
        if not audio_bytes:
            return

        if self._play_fn is not None:
            self._play_fn(audio_bytes, mime_type=mime_type)
            return

        # Windows MCI playback
        suffix = ".mp3" if "mpeg" in mime_type or "mp3" in mime_type else ".wav"
        temp_fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="mamba_speech_")
        try:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(audio_bytes)

            self._play_file_mci(temp_path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

    def _play_file_mci(self, file_path: str) -> None:
        """Use Windows winmm.dll MCI to play audio synchronously."""
        alias = f"mamba_audio_{int(time.time() * 1000)}"

        try:
            mci = ctypes.windll.winmm.mciSendStringW
        except AttributeError as exc:
            raise AudioPlaybackError(
                f"Windows MCI playback is not available on this platform: {exc}"
            ) from exc

        # Open
        open_cmd = f'open "{file_path}" type mpegvideo alias {alias}'
        res_open = mci(open_cmd, None, 0, None)
        if res_open != 0:
            raise AudioPlaybackError(f"failed to open audio device (MCI error code {res_open})")

        try:
            # Play synchronously
            play_cmd = f"play {alias} wait"
            res_play = mci(play_cmd, None, 0, None)
            if res_play != 0:
                raise AudioPlaybackError(f"failed to play audio (MCI error code {res_play})")
        finally:
            # Always close alias
            mci(f"close {alias}", None, 0, None)

