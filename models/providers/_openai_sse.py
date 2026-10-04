"""Shared SSE (text/event-stream) delta parsing for OpenAI-compatible providers.

Groq and NVIDIA both talk to an OpenAI-compatible ``/chat/completions``
endpoint, so the streamed wire format is identical: ``data: {json}`` lines
followed by ``data: [DONE]``.
"""

from __future__ import annotations

import json

DONE = "__DONE__"


def parse_sse_delta(line: str, key: str = "content") -> str | None:
    """Return the text delta carried by one SSE line, ``DONE``, or ``None``.

    ``key`` selects which ``choices[0].delta`` field to read. Callers pass
    ``reasoning_content`` only to collect thinking text out of band — it must
    never be forwarded to a user-facing sink.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if not stripped.startswith("data:"):
        return None
    payload = stripped[len("data:"):].strip()
    if payload == "[DONE]":
        return DONE
    try:
        obj = json.loads(payload)
    except ValueError:
        return None
    choices = obj.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    return delta.get(key) or None
