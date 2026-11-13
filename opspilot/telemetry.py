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
        self.log_services = {"gateway": self.gateway, **self.services}
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
        failed_services: dict[str, str] = {}
        service_status: dict[str, str] = {}
        per_service = max(1, min(1000, limit))
        for service, base in self.log_services.items():
            try:
                response = self._get(base, "/logs", {"start": start, "end": end, "limit": per_service})
            except (OSError, ValueError) as exc:
                failed_services[service] = type(exc).__name__
                service_status[service] = "error"
                continue
            rows = response.get("records")
            if not isinstance(rows, list):
                failed_services[service] = "InvalidRecords"
                service_status[service] = "error"
                continue
            service_status[service] = "ok"
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("timestamp"), str):
                    continue
                try:
                    instant = _instant(row["timestamp"])
                except ValueError:
                    continue
                if not lower <= instant <= upper:
                    continue
                records.append({"record_id": _record_id("logs", {"source_service": service, "record": row}), "timestamp": instant.isoformat().replace("+00:00", "Z"),
                    "source": "logs", "service": service, "trace_id": row.get("trace_id"),
                    "attributes": {key: value for key, value in row.items() if key not in {"timestamp", "message"}},
                    "raw": row})
        records.sort(key=lambda row: (row["timestamp"], row["record_id"]))
        warnings = ["partial_service_unavailable"] if failed_services else []
        return {"source": "logs", "query": {"start": start, "end": end, "limit": limit},
            "records": records[:limit], "warnings": warnings, "failed_services": failed_services,
            "service_status": service_status}

    def deployments(self, start: str, end: str, limit: int = 100) -> dict[str, Any]:
        self._bounds(start, end, limit)
        response = self._get(self.gateway, "/deployments", {"start": start, "end": end, "limit": limit})
        source_rows = response.get("records")
        if not isinstance(source_rows, list):
            raise ValueError("deployment response records must be a list")
        lower, upper = _instant(start), _instant(end)
        rows = []
        source_order = []
        for row in source_rows:
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
        failed_services: dict[str, str] = {}
        service_status: dict[str, str] = {}
        for service in SERVICES:
            try:
                response = self._get(self.jaeger, "/api/traces", {
                    "service": service, "start": int(lower.timestamp() * 1_000_000),
                    "end": int(upper.timestamp() * 1_000_000), "limit": limit,
                })
            except (OSError, ValueError) as exc:
                failed_services[service] = type(exc).__name__
                service_status[service] = "error"
                continue
            trace_rows = response.get("data")
            if not isinstance(trace_rows, list):
                failed_services[service] = "InvalidTraceData"
                service_status[service] = "error"
                continue
            service_status[service] = "ok"
            for trace_row in trace_rows:
                if not isinstance(trace_row, dict):
                    continue
                trace_id = trace_row.get("traceID")
                if not isinstance(trace_id, str) or not trace_id.strip():
                    continue
                processes = trace_row.get("processes", {})
                spans = trace_row.get("spans", [])
                if not isinstance(processes, dict) or not isinstance(spans, list):
                    continue
                for span in spans:
                    if not isinstance(span, dict):
                        continue
                    span_id = span.get("spanID")
                    if not isinstance(span_id, str) or not span_id.strip():
                        continue
                    try:
                        timestamp = datetime.fromtimestamp(float(span["startTime"]) / 1_000_000, timezone.utc).isoformat().replace("+00:00", "Z")
                    except (KeyError, TypeError, ValueError, OverflowError, OSError):
                        continue
                    process = processes.get(span.get("processID"), {})
                    if not isinstance(process, dict):
                        process = {}
                    tags_rows = span.get("tags", [])
                    refs = span.get("references", [])
                    tags = {tag.get("key"): tag.get("value") for tag in tags_rows if isinstance(tag, dict)} if isinstance(tags_rows, list) else {}
                    parent_span_id = next((ref.get("spanID") for ref in refs if isinstance(ref, dict) and ref.get("refType") == "CHILD_OF"), None) if isinstance(refs, list) else None
                    raw = {"trace_id": trace_id, "span_id": span.get("spanID"), "parent_span_id": parent_span_id,
                        "operation": span.get("operationName"), "duration_us": span.get("duration"), "service": process.get("serviceName"), "attributes": tags}
                    normalized = {"record_id": _record_id("traces", raw), "timestamp": timestamp,
                        "source": "traces", "service": process.get("serviceName", service), "trace_id": trace_id,
                        "attributes": raw, "raw": raw}
                    records_by_id.setdefault(normalized["record_id"], normalized)
        records = list(records_by_id.values())
        records = [row for row in records if lower <= _instant(row["timestamp"]) <= upper]
        records.sort(key=lambda row: (row["timestamp"], row["record_id"]))
        warnings = ["partial_service_unavailable"] if failed_services else []
        return {"source": "traces", "query": {"start": start, "end": end, "limit": limit},
            "records": records[:limit], "warnings": warnings, "failed_services": failed_services,
            "service_status": service_status}
