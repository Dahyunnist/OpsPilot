"""Bounded trace export flush used by deterministic telemetry scenarios."""

from __future__ import annotations

from typing import Any


def flush_fault_trace(provider: Any) -> bool:
    """Flush queued spans before returning a slow SQL lab response."""
    if provider is None:
        return True
    try:
        return bool(provider.force_flush(timeout_millis=5000))
    except Exception:
        return False
