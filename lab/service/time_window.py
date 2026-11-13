"""UTC timestamp parsing and half-open window matching for lab telemetry APIs."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_utc(value: str) -> datetime:
    if not value:
        raise ValueError("start and end are required")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return instant.astimezone(timezone.utc)


def in_window(timestamp: str, start: str, end: str) -> bool:
    lower, upper = parse_utc(start), parse_utc(end)
    try:
        value = parse_utc(timestamp)
    except (TypeError, ValueError):
        return False
    return lower <= value < upper
