"""Normalize a small OpenTelemetry JSON fixture for offline correlation tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def normalize_otel_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for signal in ("traces", "logs", "metrics"):
        for row in payload.get(signal, []):
            attrs = row.get("attributes", {})
            resource = row.get("resource", {})
            trace_id = row.get("trace_id") or row.get("span", {}).get("trace_id")
            timestamp = row.get("timestamp") or row.get("time_unix_nano")
            if isinstance(timestamp, int):
                timestamp = datetime.fromtimestamp(timestamp / 1_000_000_000, timezone.utc).isoformat().replace("+00:00", "Z")
            if not isinstance(timestamp, str):
                continue
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    continue
            except ValueError:
                continue
            service = resource.get("service.name") or row.get("service")
            if not service:
                continue
            records.append({"signal": signal, "trace_id": trace_id, "service": service,
                            "timestamp": parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "attributes": attrs})
    return records
