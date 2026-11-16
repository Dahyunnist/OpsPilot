"""Correlate the lab's windowed logs and Jaeger spans for one synthetic order."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.service.time_window import in_window


def _span_timestamp(value: Any) -> str | None:
    try:
        instant = datetime.fromtimestamp(float(value) / 1_000_000, timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


def verify_slow_sql_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    order_id = str(payload.get("order_id", ""))
    start = str(payload.get("window_start", ""))
    end = str(payload.get("window_end", ""))
    log_response = payload.get("logs", {})
    trace_response = payload.get("traces", {})
    log_rows = log_response.get("records", []) if isinstance(log_response, dict) else []
    trace_rows = []
    if isinstance(trace_response, dict):
        window_response = trace_response.get("window_query", trace_response)
        if isinstance(window_response, dict) and isinstance(window_response.get("data"), list):
            trace_rows.extend(window_response["data"])
        for query in trace_response.get("by_trace_id", []):
            if not isinstance(query, dict) or query.get("status") not in (None, 200):
                continue
            response = query.get("response", {}) if isinstance(query, dict) else {}
            if isinstance(response, dict) and isinstance(response.get("data"), list):
                trace_rows.extend(response["data"])

    logs = []
    for row in log_rows:
        if not isinstance(row, dict) or row.get("order_id") != order_id:
            continue
        if not in_window(row.get("timestamp", ""), start, end):
            continue
        logs.append({key: row.get(key) for key in (
            "timestamp", "service", "message", "order_id", "trace_id", "span_id",
        )})

    trace_ids = sorted({str(row["trace_id"]).lower() for row in logs
                        if isinstance(row.get("trace_id"), str) and row["trace_id"]})
    spans = []
    for trace in trace_rows:
        if not isinstance(trace, dict):
            continue
        trace_id = trace.get("traceID")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        processes = trace.get("processes", {})
        rows = trace.get("spans", [])
        if not isinstance(processes, dict) or not isinstance(rows, list):
            continue
        for span in rows:
            if not isinstance(span, dict):
                continue
            span_start = _span_timestamp(span.get("startTime"))
            if not span_start or not in_window(span_start, start, end):
                continue
            process = processes.get(span.get("processID"), {})
            if not isinstance(process, dict):
                continue
            tags = span.get("tags", [])
            if not isinstance(tags, list):
                continue
            order_tags = [tag for tag in tags if isinstance(tag, dict)
                          and tag.get("key") == "opspilot.order_id"]
            if not order_tags or str(order_tags[0].get("value", "")) != order_id:
                continue
            spans.append({
                "trace_id": trace_id.lower(),
                "operation": span.get("operationName"),
                "duration_us": span.get("duration"),
                "service": process.get("serviceName"),
                "order_id": order_id,
                "timestamp": span_start,
                "linked_to_log": trace_id.lower() in trace_ids,
            })

    slow_queries = [span for span in spans
                    if span["service"] == "orders"
                    and span["operation"] == "postgres.query"
                    and isinstance(span["duration_us"], (int, float))
                    and span["duration_us"] >= 1_200_000
                    and span["linked_to_log"]]
    correlated = bool(logs and trace_ids and slow_queries)
    if correlated:
        failure_stage = None
    elif not logs:
        failure_stage = "no_order_logs"
    elif not trace_ids:
        failure_stage = "log_missing_trace_id"
    elif not spans:
        failure_stage = "jaeger_no_order_spans"
    elif not slow_queries:
        matching_queries = [span for span in spans if span["operation"] == "postgres.query"]
        failure_stage = "db_span_not_slow" if matching_queries else "jaeger_no_postgres_query"
    else:
        failure_stage = "slow_query_trace_unlinked"
    error = None if correlated else (
        f"No slow postgres.query span correlated; stage={failure_stage}, logs={len(logs)}, spans={len(spans)}."
    )
    return {
        "query_window": {"start": start, "end": end},
        "order_id": order_id,
        "logs": logs,
        "trace_ids": trace_ids,
        "traces": spans,
        "correlated": correlated,
        "failure_stage": failure_stage,
        "error": error,
        "jaeger_query": payload.get("jaeger_query"),
        "raw_jaeger_response": trace_response,
    }


def main() -> None:
    payload = json.load(sys.stdin)
    json.dump(verify_slow_sql_evidence(payload), sys.stdout, ensure_ascii=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
