"""Lightweight, optional latency instrumentation for Mamba's execution path.

Enabled only when the MAMBA_TIMING environment variable is truthy (or via
set_enabled for tests). Records durations (milliseconds) and safe
identifiers only — never user text, credentials, page contents, or memory.
When disabled, every helper is a no-op, so instrumentation cannot change
program behavior.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

_ENABLED = os.environ.get("MAMBA_TIMING", "0").strip().lower() not in (
    "", "0", "false", "no", "off",
)


def is_enabled() -> bool:
    return _ENABLED


def set_enabled(flag: bool) -> None:
    global _ENABLED
    _ENABLED = bool(flag)


def read(metadata: dict[str, Any]) -> dict[str, float]:
    """Return the recorded latency map (empty when nothing recorded)."""
    lat = metadata.get("latency_ms")
    return lat if isinstance(lat, dict) else {}


def mark(metadata: dict[str, Any], name: str, dt_seconds: float) -> None:
    if not _ENABLED:
        return
    lat = metadata.setdefault("latency_ms", {})
    lat[name] = round(lat.get(name, 0.0) + dt_seconds * 1000.0, 1)


@contextmanager
def span(metadata: dict[str, Any], name: str) -> Iterator[None]:
    if not _ENABLED:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        mark(metadata, name, time.perf_counter() - t0)
