"""Google Gemini model provider for Mamba.

Connects Mamba's model abstraction to the Google Gemini API using the
official google-genai SDK.

Default model: gemini-2.5-flash (fast, multimodal-capable).
"""

from __future__ import annotations

import os
from typing import Any

from ..errors import ModelProviderError
from ..provider import BaseModelProvider
from ..types import (
    ModelImagePart,
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelTextPart,
)

_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_TIMEOUT_SECONDS = 60
_ENV_KEY = "GEMINI_API_KEY"
_FALLBACK_ENV_KEY = "GOOGLE_API_KEY"
_ENV_MODEL = "GEMINI_MODEL"


class GeminiModelProvider(BaseModelProvider):
    """Concrete ModelProvider for Google Gemini.

    Sends generate-content requests to the Gemini API via the official
    google-genai SDK and maps responses into Mamba's ModelResponse type.

    Configuration:
        api_key:              Gemini API key (or read from GEMINI_API_KEY / GOOGLE_API_KEY env var).
        model:                Gemini model identifier (default: gemini-2.5-flash,
                              or read from GEMINI_MODEL env var).
        timeout:              Request timeout in seconds (default: 60).
        supports_multimodal:  When True, advertise multimodal capability and
                              serialize image message parts for vision-capable models.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        supports_multimodal: bool = True,
        _client: Any = None,
    ) -> None:
        resolved_key = (
            api_key
            or os.environ.get(_ENV_KEY, "").strip()
            or os.environ.get(_FALLBACK_ENV_KEY, "").strip()
        )
        if not resolved_key:
            raise ModelProviderError(
                f"Gemini API key is required: set {_ENV_KEY} environment "
                f"variable or pass api_key to the provider"
            )

        resolved_model = model or os.environ.get(_ENV_MODEL, "").strip() or _DEFAULT_MODEL

        self._api_key = resolved_key
        self._model = resolved_model
        self._timeout = timeout
        self._supports_multimodal = supports_multimodal

        # Build the SDK client (injectable for testing).
        if _client is not None:
            self._client = _client
        else:
            try:
                from google import genai

                self._client = genai.Client(
                    api_key=resolved_key,
                    http_options={"timeout": timeout * 1000},
                )
            except ImportError as exc:
                raise ModelProviderError(
                    "google-genai SDK is not installed: "
                    "pip install google-genai"
                ) from exc
            except Exception as exc:
                raise ModelProviderError(
                    self._sanitize(f"failed to create Gemini client: {exc}")
                ) from exc

        capabilities: dict[str, Any] = {
            "chat": True,
            "text_generation": True,
        }
        if supports_multimodal:
            capabilities["multimodal"] = True
            capabilities["image"] = True

        info = ModelInfo(
            provider="gemini",
            model=self._model,
            capabilities=capabilities,
        )
        super().__init__(info)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Send a generate-content request to the Gemini API."""
        if request.has_images and not self._supports_multimodal:
            raise ModelProviderError(
                "configured Gemini model does not support multimodal/image input"
            )

        model_to_use = request.model_id or self._model
        contents = self._build_contents(request)
        config = self._build_config(request.parameters, request.system_instruction)

        try:
            from google.genai import types as genai_types

            kwargs: dict[str, Any] = {
                "model": model_to_use,
                "contents": contents,
            }
            if config:
                kwargs["config"] = genai_types.GenerateContentConfig(**config)

            response = self._client.models.generate_content(**kwargs)
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"Gemini API request failed: {exc}")
            ) from exc

        return self._parse_response(response, fallback_model=model_to_use)

    def _sanitize(self, message: str) -> str:
        """Strip API keys from any error message."""
        if self._api_key and self._api_key in message:
            message = message.replace(self._api_key, "***")
        for env_var in (_ENV_KEY, _FALLBACK_ENV_KEY):
            key = os.environ.get(env_var, "").strip()
            if key and key in message:
                message = message.replace(key, "***")
        return message

    # ── Request construction ──

    def _build_contents(
        self, request: ModelRequest,
    ) -> list[Any]:
        """Convert a ModelRequest into Gemini contents list."""
        from google.genai import types as genai_types

        contents: list[Any] = []

        if request.messages:
            for msg in request.messages:
                parts = self._serialize_parts(msg.content)
                role = "model" if msg.role == "assistant" else msg.role
                contents.append(genai_types.Content(
                    role=role,
                    parts=parts,
                ))
            # Append trailing input as a final user turn if distinct.
            if request.input:
                last_text = ""
                if contents:
                    last_parts = contents[-1].parts
                    if last_parts and hasattr(last_parts[-1], "text"):
                        last_text = last_parts[-1].text or ""
                if request.input != last_text:
                    contents.append(genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=request.input)],
                    ))
        elif request.input:
            contents.append(genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=request.input)],
            ))

        if not contents:
            raise ModelProviderError("request has no input or messages")

        return contents

    def _serialize_parts(
        self, content: str | tuple[Any, ...],
    ) -> list[Any]:
        """Serialize message content into Gemini Part objects."""
        from google.genai import types as genai_types

        if isinstance(content, str):
            return [genai_types.Part.from_text(text=content)]

        parts: list[Any] = []
        for part in content:
            if isinstance(part, ModelTextPart):
                parts.append(genai_types.Part.from_text(text=part.text))
            elif isinstance(part, ModelImagePart):
                if not self._supports_multimodal:
                    raise ModelProviderError(
                        "configured Gemini model does not support "
                        "multimodal/image input"
                    )
                parts.append(genai_types.Part.from_bytes(
                    data=part.data,
                    mime_type=part.media_type,
                ))
            else:
                raise ModelProviderError(
                    f"unsupported model content part type: {type(part).__name__}"
                )

        if not parts:
            raise ModelProviderError("message content parts are empty")
        return parts

    def _build_config(
        self,
        parameters: dict[str, Any],
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Build GenerateContentConfig kwargs from request parameters and system instruction."""
        from google.genai import types as genai_types

        config: dict[str, Any] = {
            "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(disable=True),
        }

        if system_instruction and system_instruction.strip():
            config["system_instruction"] = system_instruction.strip()

        # Map supported parameters to Gemini config fields.
        param_map = {
            "temperature": "temperature",
            "top_p": "top_p",
            "top_k": "top_k",
            "max_tokens": "max_output_tokens",
            "stop": "stop_sequences",
            "presence_penalty": "presence_penalty",
            "frequency_penalty": "frequency_penalty",
        }
        for mamba_key, gemini_key in param_map.items():
            if mamba_key in parameters:
                config[gemini_key] = parameters[mamba_key]

        return config

    # ── Response parsing ──

    def _parse_response(
        self, response: Any, *, fallback_model: str,
    ) -> ModelResponse:
        """Parse a Gemini SDK response into a ModelResponse."""
        try:
            # Extract text content from the response.
            text = response.text
            if not text:
                raise ModelProviderError(
                    "Gemini API returned empty content in response"
                )

            # Build metadata from usage info if available.
            metadata: dict[str, Any] = {}
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = response.usage_metadata
                metadata["usage"] = {
                    "prompt_tokens": getattr(usage, "prompt_token_count", None),
                    "completion_tokens": getattr(usage, "candidates_token_count", None),
                    "total_tokens": getattr(usage, "total_token_count", None),
                }

            # Extract finish reason if available.
            if (
                hasattr(response, "candidates")
                and response.candidates
                and hasattr(response.candidates[0], "finish_reason")
            ):
                finish_reason = response.candidates[0].finish_reason
                if finish_reason is not None:
                    metadata["finish_reason"] = str(finish_reason)

            # Use model name from response if available.
            response_model = getattr(response, "model", None) or fallback_model
            # Response model may include prefix like "models/"; strip it.
            if isinstance(response_model, str) and response_model.startswith("models/"):
                response_model = response_model[len("models/"):]

            return ModelResponse(
                content=text,
                provider="gemini",
                model=response_model,
                success=True,
                metadata=metadata,
            )

        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError(
                self._sanitize(f"failed to parse Gemini API response: {exc}")
            ) from exc
