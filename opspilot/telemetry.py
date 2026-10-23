"""Bounded, loopback-only read adapters for the local fault lab telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from typing import Any


MAX_WINDOW_SECONDS = 24 * 60 * 60
MAX_RESPONSE_BYTES = 2_000_000
SERVICES = ("gateway", "orders", "inventory", "payment")


def _instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("start and end must be ISO 8601 timestamps")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("start and end must be ISO 8601 timestamps") from exc
    if parsed.tzinfo is None:
        raise ValueError("start and end must include a timezone")
    return parsed.astimezone(timezone.utc)


def _safe_base(value: str, service: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{service} endpoint must use HTTP loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{service} endpoint contains unsupported URL fields")
    return value.rstrip("/")


def _record_id(source: str, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"{source}:{hashlib.sha256(encoded).hexdigest()[:20]}"


class LabTelemetry:
    def __init__(self, *, prometheus: str | None = None, jaeger: str | None = None,
                 gateway: str | None = None, services: dict[str, str] | None = None,
                 timeout_seconds: float = 3.0) -> None:
        self.prometheus = _safe_base(prometheus or os.getenv("OPSPILOT_PROMETHEUS_URL", "http://127.0.0.1:19090"), "Prometheus")
        self.jaeger = _safe_base(jaeger or os.getenv("OPSPILOT_JAEGER_URL", "http://127.0.0.1:16686"), "Jaeger")
        self.gateway = _safe_base(gateway or os.getenv("OPSPILOT_LAB_GATEWAY_URL", "http://127.0.0.1:18080"), "gateway")
        configured = services or {}
        self.services = {
            name: _safe_base(configured.get(name, os.getenv(f"OPSPILOT_LAB_{name.upper()}_URL", f"http://127.0.0.1:{18081 + index}")), name)
            for index, name in enumerate(SERVICES[1:])
        }
        self.timeout_seconds = min(10.0, max(0.2, float(timeout_seconds)))

    def _get(self, base: str, path: str, query: dict[str, Any]) -> dict[str, Any]:
        request = Request(f"{base}{path}?{urlencode(query)}", method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("telemetry response exceeds 2 MB")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("telemetry response must be a JSON object")
        return parsed

    @staticmethod
    def _bounds(start: str, end: str, limit: int) -> tuple[datetime, datetime, int]:
        lower, upper = _instant(start), _instant(end)
        duration = (upper - lower).total_seconds()
        if duration <= 0 or duration > MAX_WINDOW_SECONDS:
            raise ValueError("time window must be positive and no longer than 24 hours")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return lower, upper, limit

    def metrics(self, start: str, end: str, limit: int = 100) -> dict[str, Any]:
        lower, upper, limit = self._bounds(start, end, limit)
        duration = (upper - lower).total_seconds()
        step = max(2, int(duration * 30 / limit))
        response = self._get(self.prometheus, "/api/v1/query_range", {
            "query": "{__name__=~\"opspilot_http_(requests|errors)_total|opspilot_http_duration_ms_sum\"}",
            "start": lower.timestamp(), "end": upper.timestamp(), "step": f"{step}s",
        })
        if response.get("status") != "success":
            raise ValueError("Prometheus rejected the bounded query")
        records = []
        for series in response.get("data", {}).get("result", [])[:30]:
            metric = series.get("metric", {})
            name = metric.get("__name__")
            service = metric.get("service")
            version = metric.get("version")
            for stamp, value in series.get("values", []):
                instant = datetime.fromtimestamp(float(stamp), timezone.utc)
                if not lower <= instant <= upper:
                    continue
                row = {"name": name, "service": service, "version": version, "value": value}
                records.append({"record_id": _record_id("metrics", [metric, stamp, value]),
                    "timestamp": instant.isoformat().replace("+00:00", "Z"),
                    "source": "metrics", "service": service, "trace_id": None, "attributes": row, "raw": row})
        records.sort(key=lambda row: (row["timestamp"], row["record_id"]))
        return {"source": "metrics", "query": {"start": start, "end": end, "limit": limit, "step_seconds": step}, "records": records[:limit]}

    def logs(self, start: str, end: str, limit: int = 100) -> dict[str, Any]:
        lower, upper, limit = self._bounds(start, end, limit)
        records = []
        per_service = max(1, min(1000, limit))
        for service, base in self.services.items():
            response = self._get(base, "/logs", {"start": start, "end": end, "limit": per_service})
            for row in response.get("records", [])[:per_service]:
                if not isinstance(row, dict) or not isinstance(row.get("timestamp"), str):
                    continue
                try:
                    instant = _instant(row["timestamp"])
                except ValueError:
                    continue
                if not lower <= instant <= upper:
                    continue
                records.append({"record_id": _record_id("logs", row), "timestamp": instant.isoformat().replace("+00:00", "Z"),
                    "source": "logs", "service": service, "trace_id": row.get("trace_id"),
                    "attributes": {key: value for key, value in row.items() if key not in {"timestamp", "message"}},
                    "raw": row})
        records.sort(key=lambda row: (row["timestamp"], row["record_id"]))
        return {"source": "logs", "query": {"start": start, "end": end, "limit": limit}, "records": records[:limit]}

    def deployments(self, start: str, end: str, limit: int = 100) -> dict[str, Any]:
        self._bounds(start, end, limit)
        response = self._get(self.gateway, "/deployments", {"start": start, "end": end, "limit": limit})
        lower, upper = _instant(start), _instant(end)
        rows = []
        source_order = []
        for row in response.get("records", [])[:limit]:
            if not isinstance(row, dict):
                continue
            try:
                instant = _instant(row.get("timestamp"))
            except ValueError:
                continue
            if lower <= instant <= upper:
                source_order.append(instant)
                rows.append({"record_id": row.get("record_id") or _record_id("deployments", row),
                    "timestamp": instant.isoformat().replace("+00:00", "Z"), "source": "deployments",
                    "service": row.get("service"), "trace_id": None,
                    "attributes": {key: value for key, value in row.items() if key != "timestamp"}, "raw": row})
        rows.sort(key=lambda row: (row["timestamp"], row["record_id"]))
        out_of_order = any(right < left for left, right in zip(source_order, source_order[1:]))
        warnings = ["source_order_out_of_order"] if out_of_order else []
        return {"source": "deployments", "query": {"start": start, "end": end, "limit": limit}, "records": rows[:limit], "warnings": warnings}

    def traces(self, start: str, end: str, limit: int = 100) -> dict[str, Any]:
        lower, upper, limit = self._bounds(start, end, limit)
        records_by_id = {}
        for service in SERVICES:
            response = self._get(self.jaeger, "/api/traces", {
                "service": service, "start": int(lower.timestamp() * 1_000_000),
                "end": int(upper.timestamp() * 1_000_000), "limit": limit,
            })
            for trace_row in response.get("data", []):
                trace_id = trace_row.get("traceID")
                processes = trace_row.get("processes", {})
                for span in trace_row.get("spans", []):
                    timestamp = datetime.fromtimestamp(span["startTime"] / 1_000_000, timezone.utc).isoformat().replace("+00:00", "Z")
                    process = processes.get(span.get("processID"), {})
                    tags = {tag.get("key"): tag.get("value") for tag in span.get("tags", []) if isinstance(tag, dict)}
                    raw = {"trace_id": trace_id, "span_id": span.get("spanID"), "parent_span_id": next((ref.get("spanID") for ref in span.get("references", []) if ref.get("refType") == "CHILD_OF"), None),
                        "operation": span.get("operationName"), "duration_us": span.get("duration"), "service": process.get("serviceName"), "attributes": tags}
                    normalized = {"record_id": _record_id("traces", raw), "timestamp": timestamp,
                        "source": "traces", "service": process.get("serviceName", service), "trace_id": trace_id,
                        "attributes": raw, "raw": raw}
                    records_by_id.setdefault(normalized["record_id"], normalized)
        records = list(records_by_id.values())
        records = [row for row in records if lower <= _instant(row["timestamp"]) <= upper]
        records.sort(key=lambda row: (row["timestamp"], row["record_id"]))
        return {"source": "traces", "query": {"start": start, "end": end, "limit": limit}, "records": records[:limit]}
