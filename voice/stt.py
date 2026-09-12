"""Groq Whisper speech-to-text provider."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from .errors import STTError

_DEFAULT_MODEL = "whisper-large-v3-turbo"
_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
_DEFAULT_TIMEOUT_SECONDS = 30
_ENV_KEY = "GROQ_API_KEY"
_ENV_MODEL = "GROQ_STT_MODEL"


class GroqSTTProvider:
    """Speech-to-text provider using Groq's hosted Whisper API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        _http_post: Any = None,
    ) -> None:
        resolved_key = api_key or os.environ.get(_ENV_KEY, "").strip()
        if not resolved_key:
            raise STTError(
                f"Groq API key is required for speech-to-text: set {_ENV_KEY} environment "
                f"variable or pass api_key to the provider"
            )

        resolved_model = model or os.environ.get(_ENV_MODEL, "").strip() or _DEFAULT_MODEL

        self._api_key = resolved_key
        self._model = resolved_model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http_post = _http_post or self._default_http_post

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, audio_bytes: bytes, *, mime_type: str = "audio/wav") -> str:
        """Submit audio bytes to Groq Whisper and return transcribed text."""
        if not audio_bytes:
            raise STTError("audio data is empty")

        url = f"{self._base_url}/audio/transcriptions"
        boundary = f"----MambaVoiceBoundary{uuid.uuid4().hex}"

        filename = "recording.wav" if "wav" in mime_type else "recording.mp3"
        body = self._build_multipart_body(
            boundary=boundary,
            fields={"model": self._model},
            files={"file": (filename, mime_type, audio_bytes)},
        )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mamba-AI/1.0",
        }

        try:
            raw_response = self._http_post(
                url=url,
                headers=headers,
                data=body,
                timeout=self._timeout,
            )
        except STTError:
            raise
        except Exception as exc:
            raise STTError(self._sanitize(f"Groq STT request failed: {exc}")) from exc

        return self._parse_response(raw_response)

    def _build_multipart_body(
        self,
        *,
        boundary: str,
        fields: dict[str, str],
        files: dict[str, tuple[str, str, bytes]],
    ) -> bytes:
        """Construct multipart/form-data payload."""
        lines: list[bytes] = []

        for field_name, value in fields.items():
            lines.extend([
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{field_name}"'.encode("utf-8"),
                b"",
                str(value).encode("utf-8"),
            ])

        for file_key, (filename, content_type, file_bytes) in files.items():
            lines.extend([
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{file_key}"; filename="{filename}"'.encode("utf-8"),
                f"Content-Type: {content_type}".encode("utf-8"),
                b"",
                file_bytes,
            ])

        lines.append(f"--{boundary}--".encode("utf-8"))
        lines.append(b"")
        return b"\r\n".join(lines)

    def _parse_response(self, raw: dict[str, Any]) -> str:
        """Extract text from Groq response."""
        if not isinstance(raw, dict):
            raise STTError(f"unexpected response format from Groq STT: {type(raw).__name__}")

        if "error" in raw:
            err = raw["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise STTError(self._sanitize(f"Groq STT error: {err_msg}"))

        text = raw.get("text", "")
        return text.strip()

    def _sanitize(self, message: str) -> str:
        """Strip API keys from error messages."""
        if self._api_key and self._api_key in message:
            message = message.replace(self._api_key, "***")
        env_key = os.environ.get(_ENV_KEY, "").strip()
        if env_key and env_key in message:
            message = message.replace(env_key, "***")
        return message

    def _default_http_post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        data: bytes,
        timeout: int,
    ) -> dict[str, Any]:
        """Send HTTP POST with multipart payload."""
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise STTError(
                self._sanitize(f"Groq STT HTTP {exc.code}: {err_body[:400] if err_body else exc.reason}")
            ) from exc
        except urllib.error.URLError as exc:
            raise STTError(self._sanitize(f"Groq STT network error: {exc.reason}")) from exc
        except TimeoutError as exc:
            raise STTError(f"Groq STT request timed out after {timeout}s") from exc
        except Exception as exc:
            raise STTError(self._sanitize(f"Groq STT connection failed: {exc}")) from exc

        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise STTError(f"failed to parse Groq STT response JSON: {exc}") from exc

