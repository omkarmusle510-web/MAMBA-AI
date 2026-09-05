"""NVIDIA NIM model provider for Mamba.

Connects Mamba's model abstraction to the NVIDIA hosted API
(build.nvidia.com) using the OpenAI-compatible chat completions endpoint.

Default model: Nemotron 3.5 Lightning 30B (free tier, agentic-optimized).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ..errors import ModelProviderError
from ..provider import BaseModelProvider
from ..types import ModelInfo, ModelMessage, ModelRequest, ModelResponse

_DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_TIMEOUT_SECONDS = 60
_ENV_KEY = "NVIDIA_API_KEY"


class NVIDIAModelProvider(BaseModelProvider):
    """Concrete ModelProvider for NVIDIA NIM (Nemotron models).

    Sends chat completion requests to the NVIDIA hosted API and maps
    responses into Mamba's existing ModelResponse type.

    Configuration:
        api_key:    NVIDIA API key (or read from NVIDIA_API_KEY env var).
        model:      NVIDIA model identifier (default: Nemotron 3.5 Lightning).
        base_url:   API base URL (default: integrate.api.nvidia.com/v1).
        timeout:    HTTP timeout in seconds (default: 60).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        _http_post: Any = None,
    ) -> None:
        resolved_key = api_key or os.environ.get(_ENV_KEY, "")
        if not resolved_key:
            raise ModelProviderError(
                f"NVIDIA API key is required: set {_ENV_KEY} environment "
                f"variable or pass api_key to the provider"
            )

        self._api_key = resolved_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Injectable HTTP boundary for testing without network access.
        self._http_post = _http_post or self._default_http_post

        info = ModelInfo(
            provider="nvidia",
            model=self._model,
            capabilities={
                "chat": True,
                "text_generation": True,
            },
        )
        super().__init__(info)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Send a chat completion request to the NVIDIA API."""
        model_to_use = request.model_id or self._model
        messages = self._build_messages(request)
        body = self._build_body(model_to_use, messages, request.parameters)

        try:
            raw = self._http_post(
                url=f"{self._base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                body=body,
                timeout=self._timeout,
            )
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"NVIDIA API request failed: {exc}")
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
    ) -> list[dict[str, str]]:
        """Convert a ModelRequest into the chat messages list."""
        messages: list[dict[str, str]] = []

        if request.system_instruction:
            messages.append({
                "role": "system",
                "content": request.system_instruction,
            })

        if request.messages:
            for msg in request.messages:
                messages.append({"role": msg.role, "content": msg.content})
            if request.input and (not messages or messages[-1]["content"] != request.input):
                messages.append({"role": "user", "content": request.input})
        elif request.input:
            messages.append({"role": "user", "content": request.input})

        if not messages:
            raise ModelProviderError("request has no input or messages")

        return messages

    def _build_body(
        self,
        model: str,
        messages: list[dict[str, str]],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the JSON body for the NVIDIA API."""
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
        """Parse the NVIDIA API JSON response into a ModelResponse."""
        try:
            choices = raw.get("choices")
            if not choices:
                raise ModelProviderError(
                    "NVIDIA API returned no choices in response"
                )

            first_choice = choices[0]
            message = first_choice.get("message", {})
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""

            # If standard content is empty but reasoning is available, use reasoning
            final_content = content or reasoning
            if not final_content:
                raise ModelProviderError(
                    "NVIDIA API returned empty content in response"
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
            if reasoning and content:
                metadata["reasoning_content"] = reasoning

            finish_reason = first_choice.get("finish_reason")
            if finish_reason:
                metadata["finish_reason"] = finish_reason

            return ModelResponse(
                content=final_content,
                provider="nvidia",
                model=response_model,
                success=True,
                metadata=metadata,
            )

        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"failed to parse NVIDIA API response: {exc}")
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
                    f"NVIDIA API HTTP {exc.code}: "
                    f"{error_body[:500] if error_body else exc.reason}"
                )
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(
                self._sanitize(f"NVIDIA API network error: {exc.reason}")
            ) from exc
        except TimeoutError as exc:
            raise ModelProviderError(
                self._sanitize(f"NVIDIA API request timed out after {timeout}s")
            ) from exc
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"NVIDIA API connection failed: {exc}")
            ) from exc

        try:
            return json.loads(raw_bytes)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ModelProviderError(
                self._sanitize(f"NVIDIA API returned invalid JSON: {exc}")
            ) from exc
