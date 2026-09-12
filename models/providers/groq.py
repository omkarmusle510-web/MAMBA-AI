"""Groq model provider for Mamba.

Connects Mamba's model abstraction to the Groq Cloud API using the
OpenAI-compatible chat completions endpoint.

Default model: Qwen 3.8 27B (fast inference via Groq LPU).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ..errors import ModelProviderError
from ..provider import BaseModelProvider
from ..types import (
    ModelInfo,
    ModelRequest,
    ModelResponse,
)

_DEFAULT_MODEL = "qwen/qwen3.8-27b"
_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
_DEFAULT_TIMEOUT_SECONDS = 60
_ENV_KEY = "GROQ_API_KEY"
_ENV_MODEL = "GROQ_MODEL"


class GroqModelProvider(BaseModelProvider):
    """Concrete ModelProvider for Groq Cloud (LPU-accelerated inference).

    Sends chat completion requests to the Groq API and maps responses
    into Mamba's existing ModelResponse type.

    Configuration:
        api_key:   Groq API key (or read from GROQ_API_KEY env var).
        model:     Groq model identifier (default: qwen/qwen3.8-27b,
                   or read from GROQ_MODEL env var).
        base_url:  API base URL (default: api.groq.com/openai/v1).
        timeout:   HTTP timeout in seconds (default: 60).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        _http_post: Any = None,
    ) -> None:
        resolved_key = api_key or os.environ.get(_ENV_KEY, "")
        if not resolved_key:
            raise ModelProviderError(
                f"Groq API key is required: set {_ENV_KEY} environment "
                f"variable or pass api_key to the provider"
            )

        resolved_model = model or os.environ.get(_ENV_MODEL, "").strip() or _DEFAULT_MODEL

        self._api_key = resolved_key
        self._model = resolved_model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Injectable HTTP boundary for testing without network access.
        self._http_post = _http_post or self._default_http_post

        info = ModelInfo(
            provider="groq",
            model=self._model,
            capabilities={
                "chat": True,
                "text_generation": True,
            },
        )
        super().__init__(info)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Send a chat completion request to the Groq API."""
        if request.has_images:
            raise ModelProviderError(
                "Groq provider does not support multimodal/image input"
            )

        model_to_use = request.model_id or self._model
        messages = self._build_messages(request)
        body = self._build_body(model_to_use, messages, request.parameters)

        try:
            raw = self._http_post(
                url=f"{self._base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                    "User-Agent": "Mamba-AI/1.0",
                },
                body=body,
                timeout=self._timeout,
            )
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"Groq API request failed: {exc}")
            ) from exc

        return self._parse_response(raw, fallback_model=model_to_use)

    def _sanitize(self, message: str) -> str:
        """Strip the API key from any error message."""
        if self._api_key and self._api_key in message:
            message = message.replace(self._api_key, "***")
        env_key = os.environ.get(_ENV_KEY, "")
        if env_key and env_key in message:
            message = message.replace(env_key, "***")
        return message

    # ── Request construction ──

    def _build_messages(
        self, request: ModelRequest,
    ) -> list[dict[str, Any]]:
        """Convert a ModelRequest into the chat messages list."""
        messages: list[dict[str, Any]] = []

        if request.system_instruction:
            messages.append({
                "role": "system",
                "content": request.system_instruction,
            })

        if request.messages:
            for msg in request.messages:
                # Groq is text-only; serialize content as plain string.
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                messages.append({
                    "role": msg.role,
                    "content": content,
                })
            if request.input and (
                not messages
                or messages[-1]["content"] != request.input
            ):
                messages.append({"role": "user", "content": request.input})
        elif request.input:
            messages.append({"role": "user", "content": request.input})

        if not messages:
            raise ModelProviderError("request has no input or messages")

        return messages

    def _build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the JSON body for the Groq API."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        # Map supported parameters.
        param_keys = {
            "temperature", "top_p", "max_tokens",
            "stop", "frequency_penalty", "presence_penalty",
        }
        for key in param_keys:
            if key in parameters:
                body[key] = parameters[key]

        return body

    # ── Response parsing ──

    def _parse_response(
        self, raw: dict[str, Any], *, fallback_model: str,
    ) -> ModelResponse:
        """Parse the Groq API JSON response into a ModelResponse."""
        try:
            choices = raw.get("choices")
            if not choices:
                raise ModelProviderError(
                    "Groq API returned no choices in response"
                )

            first_choice = choices[0]
            message = first_choice.get("message", {})
            content = message.get("content") or ""

            if not content:
                raise ModelProviderError(
                    "Groq API returned empty content in response"
                )

            # Extract usage metadata when available.
            usage = raw.get("usage", {})
            response_model = raw.get("model", fallback_model)

            metadata: dict[str, Any] = {}
            if usage:
                metadata["usage"] = {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }

            finish_reason = first_choice.get("finish_reason")
            if finish_reason:
                metadata["finish_reason"] = finish_reason

            return ModelResponse(
                content=content,
                provider="groq",
                model=response_model,
                success=True,
                metadata=metadata,
            )

        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"failed to parse Groq API response: {exc}")
            ) from exc

    # ── HTTP boundary ──

    def _default_http_post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: int,
    ) -> dict[str, Any]:
        """Send an HTTP POST using the standard library."""
        encoded_body = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=encoded_body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            # Read error body for a useful message, but never leak the key.
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ModelProviderError(
                self._sanitize(
                    f"Groq API HTTP {exc.code}: "
                    f"{error_body[:500] if error_body else exc.reason}"
                )
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(
                self._sanitize(f"Groq API network error: {exc.reason}")
            ) from exc
        except TimeoutError as exc:
            raise ModelProviderError(
                self._sanitize(f"Groq API request timed out after {timeout}s")
            ) from exc
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"Groq API connection failed: {exc}")
            ) from exc

        try:
            return json.loads(raw_bytes)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ModelProviderError(
                self._sanitize(f"Groq API returned invalid JSON: {exc}")
            ) from exc
