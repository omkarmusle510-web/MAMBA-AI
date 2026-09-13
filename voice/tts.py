"""Cloudflare Workers AI text-to-speech provider."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .errors import TTSError

_DEFAULT_MODEL = "@cf/deepgram/aura-1"
_DEFAULT_TIMEOUT_SECONDS = 30
_ENV_ACCOUNT_ID = "CLOUDFLARE_ACCOUNT_ID"
_ENV_API_TOKEN = "CLOUDFLARE_API_TOKEN"
_ENV_MODEL = "CLOUDFLARE_TTS_MODEL"


class CloudflareTTSProvider:
    """Text-to-speech provider using Cloudflare Workers AI Aura-1."""

    def __init__(
        self,
        *,
        account_id: str | None = None,
        api_token: str | None = None,
        model: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        _http_post: Any = None,
    ) -> None:
        resolved_account = account_id or os.environ.get(_ENV_ACCOUNT_ID, "").strip()
        resolved_token = api_token or os.environ.get(_ENV_API_TOKEN, "").strip()

        if not resolved_account:
            raise TTSError(
                f"Cloudflare Account ID is required: set {_ENV_ACCOUNT_ID} environment "
                f"variable or pass account_id to the provider"
            )
        if not resolved_token:
            raise TTSError(
                f"Cloudflare API Token is required: set {_ENV_API_TOKEN} environment "
                f"variable or pass api_token to the provider"
            )

        resolved_model = model or os.environ.get(_ENV_MODEL, "").strip() or _DEFAULT_MODEL

        self._account_id = resolved_account
        self._api_token = resolved_token
        self._model = resolved_model
        self._timeout = timeout
        self._http_post = _http_post or self._default_http_post

    @property
    def model(self) -> str:
        return self._model

    def synthesize(self, text: str) -> bytes:
        """Submit text to Cloudflare Workers AI Aura-1 and return audio bytes."""
        if not text or not text.strip():
            raise TTSError("text to synthesize is empty")

        url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/{self._model}"
        payload = json.dumps({"text": text.strip()}).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mamba-AI/1.0",
        }

        try:
            audio_bytes = self._http_post(
                url=url,
                headers=headers,
                data=payload,
                timeout=self._timeout,
            )
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(self._sanitize(f"Cloudflare TTS request failed: {exc}")) from exc

        if not audio_bytes:
            raise TTSError("Cloudflare TTS returned empty audio response")

        return audio_bytes

    def _sanitize(self, message: str) -> str:
        """Strip API tokens and account IDs from error messages."""
        if self._api_token and self._api_token in message:
            message = message.replace(self._api_token, "***")
        if self._account_id and self._account_id in message:
            message = message.replace(self._account_id, "***")
        return message

    def _default_http_post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        data: bytes,
        timeout: int,
    ) -> bytes:
        """Send HTTP POST and return raw audio response bytes."""
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if exc.code == 429:
                raise TTSError(
                    self._sanitize(f"Cloudflare TTS HTTP 429 Quota Exceeded: {err_body[:400] if err_body else 'Rate limit / neuron quota reached'}")
                ) from exc
            raise TTSError(
                self._sanitize(f"Cloudflare TTS HTTP {exc.code}: {err_body[:400] if err_body else exc.reason}")
            ) from exc
        except urllib.error.URLError as exc:
            raise TTSError(self._sanitize(f"Cloudflare TTS network error: {exc.reason}")) from exc
        except TimeoutError as exc:
            raise TTSError(f"Cloudflare TTS request timed out after {timeout}s") from exc
        except Exception as exc:
            raise TTSError(self._sanitize(f"Cloudflare TTS connection failed: {exc}")) from exc

