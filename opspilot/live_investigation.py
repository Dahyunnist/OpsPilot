"""Evidence-linked causal triage over the local live telemetry adapters."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
import uuid

from .telemetry import LabTelemetry


CAUSES = (
    ("database_pool_exhaustion", "PostgreSQL connection pool exhaustion"),
    ("payment_dependency_timeout", "payment dependency timeout"),
    ("invalid_release_configuration", "invalid orders release configuration"),
    ("slow_database_query", "slow PostgreSQL query"),
    ("retry_amplification", "payment retry amplification"),
)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ref(record: dict[str, Any]) -> dict[str, str]:
    return {"source": record["source"], "record_id": record["record_id"]}


def _metric_records(records: list[dict[str, Any]], name_fragment: str, service: str = "orders") -> list[dict[str, Any]]:
    return [record for record in records if record.get("source") == "metrics"
        and record.get("service") == service
        and name_fragment in str(record.get("attributes", {}).get("name", ""))]


def _counter_delta(records: list[dict[str, Any]]) -> float:
    by_series: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for record in records:
        attrs = record.get("attributes", {})
        try:
            by_series[(str(attrs.get("name")), str(attrs.get("version")))].append((record["timestamp"], float(attrs.get("value", 0))))
        except (TypeError, ValueError, KeyError):
            continue
    return sum(max(0.0, max(values, key=lambda value: value[0])[1] - min(values, key=lambda value: value[0])[1])
        for values in by_series.values() if len(values) > 1)


def investigate_live(start: str, end: str, limit: int = 100,
                     telemetry: LabTelemetry | None = None) -> dict[str, Any]:
    adapter = telemetry or LabTelemetry()
    source_results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for source, query in (("metrics", adapter.metrics), ("logs", adapter.logs),
                          ("traces", adapter.traces), ("deployments", adapter.deployments)):
        try:
            source_results[source] = query(start, end, limit)
        except Exception as exc:
            failures[source] = type(exc).__name__

    records = [record for result in source_results.values() for record in result.get("records", [])]
    records.sort(key=lambda record: (record.get("timestamp", ""), record.get("record_id", "")))
    partial_failures = {source: result["failed_services"] for source, result in source_results.items()
        if isinstance(result.get("failed_services"), dict) and result["failed_services"]}
    source_status = {}
    for source, result in source_results.items():
        failed_services = result.get("failed_services", {})
        service_status = result.get("service_status", {})
        if source in failures:
            source_status[source] = "error"
        elif failed_services:
            if service_status:
                source_status[source] = "error" if not any(state == "ok" for state in service_status.values()) else "partial"
            else:
                source_status[source] = "error" if not result.get("records") else "partial"
        else:
            source_status[source] = "ok" if result.get("records") else "empty"
    source_status.update({source: "error" for source in failures})

    findings: dict[str, dict[str, Any]] = {cause: {"id": cause, "title": title, "support": [], "against": [],
        "source_set": set(), "causal_links": []}
        for cause, title in CAUSES}

    def support(cause: str, record: dict[str, Any], reason: str) -> None:
        item = {**_ref(record), "reason": reason}
        if item not in findings[cause]["support"]:
            findings[cause]["support"].append(item)
            findings[cause]["source_set"].add(record["source"])

    def against(cause: str, record: dict[str, Any], reason: str) -> None:
        item = {**_ref(record), "reason": reason}
        if item not in findings[cause]["against"]:
            findings[cause]["against"].append(item)

    orders_errors = _metric_records(records, "errors_total")
    duration_metrics = _metric_records(records, "duration_ms_sum")
    pool_logs = []
    retry_logs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row["source"] == "logs":
            raw = row.get("raw", {})
            message = str(raw.get("message", "")).lower()
            attrs = raw.get("attributes", raw)
            if row.get("service") == "orders" and "database sessions remain occupied" in message:
                support("database_pool_exhaustion", row, "orders reports occupied PostgreSQL sessions")
                pool_logs.append(row)
            if row.get("service") == "orders" and "payment attempt failed" in message and row.get("trace_id"):
                retry_logs[row["trace_id"]].append(row)
            if row.get("service") == "orders" and "payment attempt failed" in message and attrs.get("error_type") == "TimeoutError":
                support("payment_dependency_timeout", row, "orders payment call exceeded its client timeout")
            if row.get("service") == "orders" and attrs.get("dependency_host") == "inventory-v2" and attrs.get("error_type") == "URLError":
                support("invalid_release_configuration", row, "orders inventory hostname is not a configured Compose service")
            if row.get("service") == "orders" and str(attrs.get("error_type", "")).lower() in {"operationalerror", "interfaceerror"}:
                support("database_pool_exhaustion", row, "orders database request returned a connection error")
        if row["source"] == "traces":
            attrs = row.get("attributes", {})
            operation = str(attrs.get("operation", ""))
            duration_us = int(attrs.get("duration_us") or 0)
            if operation == "postgres.query" and duration_us >= 1_200_000:
                support("slow_database_query", row, f"PostgreSQL query span lasted {duration_us / 1000:.0f} ms")
            if operation == "payment/charge" and duration_us >= 350_000:
                support("payment_dependency_timeout", row, f"payment span lasted {duration_us / 1000:.0f} ms")
            if operation == "payment/charge" and duration_us < 350_000:
                against("payment_dependency_timeout", row, "payment span completed below the timeout threshold")
        if row["source"] == "deployments" and row.get("service") == "orders" and row.get("attributes", {}).get("version") == "v2":
            support("invalid_release_configuration", row, "orders v2 release is present in the incident window")

    if _counter_delta(orders_errors) > 0:
        for row in orders_errors[-1:]:
            if pool_logs:
                support("database_pool_exhaustion", row, "orders error counter increased alongside occupied database sessions")
            if any(item.get("service") == "orders" and item.get("attributes", {}).get("version") == "v2"
                   for item in records if item.get("source") == "deployments"):
                support("invalid_release_configuration", row, "orders error counter increased while orders v2 was deployed")
    else:
        for row in orders_errors[-1:]:
            against("database_pool_exhaustion", row, "orders error counter did not increase")
    if _counter_delta(duration_metrics) >= 1200:
        for row in duration_metrics[-1:]:
            support("slow_database_query", row, "orders request duration counter increased by at least 1200 ms")

    for trace_id, attempts in retry_logs.items():
        if len(attempts) >= 3:
            for row in attempts:
                support("retry_amplification", row, f"{len(attempts)} failed payment attempts share trace {trace_id}")
    payment_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        attrs = row.get("attributes", {})
        if row.get("source") == "traces" and attrs.get("operation") == "payment/charge" and row.get("trace_id"):
            payment_calls[row["trace_id"]].append(row)
    for trace_id, spans in payment_calls.items():
        if len(spans) >= 3:
            for row in spans:
                support("retry_amplification", row, f"{len(spans)} payment spans share trace {trace_id}")

    order_errors = [row for row in records if row.get("source") == "logs" and row.get("service") == "orders"
        and row.get("raw", {}).get("severity") == "error"]
    for deployment in [row for row in records if row.get("source") == "deployments" and row.get("service") == "orders"]:
        matching_errors = [error for error in order_errors
            if error.get("raw", {}).get("service_version") == deployment.get("attributes", {}).get("version")
            and error["timestamp"] >= deployment["timestamp"]
            and deployment.get("attributes", {}).get("version") == "v2"]
        for error in matching_errors:
            support("invalid_release_configuration", error, "orders v2 emitted a request error after its recorded deployment")
        later_errors = [error for error in order_errors if error["timestamp"] < deployment["timestamp"]
            and error.get("raw", {}).get("service_version") == deployment.get("attributes", {}).get("version")]
        if later_errors:
            for error in later_errors:
                against("invalid_release_configuration", error, "matching-version error predates its deployment record; check clock/order skew")

    trace_spans_by_id = {(row.get("trace_id"), row.get("attributes", {}).get("span_id")): row
        for row in records if row.get("source") == "traces" and row.get("trace_id")
        and row.get("attributes", {}).get("span_id")}
    payment_timeout_logs = [row for row in records if row.get("source") == "logs" and row.get("service") == "orders"
        and row.get("trace_id") and row.get("raw", {}).get("attributes", row.get("raw", {})).get("error_type") == "TimeoutError"]
    payment_timeout_spans = [row for row in records if row.get("source") == "traces"
        and row.get("attributes", {}).get("operation") == "payment/charge"
        and int(row.get("attributes", {}).get("duration_us") or 0) >= 350_000]
    for log in payment_timeout_logs:
        for payment_span in payment_timeout_spans:
            trace_id = log["trace_id"]
            if payment_span.get("trace_id") != trace_id:
                continue
            if abs((_instant(log["timestamp"]) - _instant(payment_span["timestamp"])).total_seconds()) > 2.0:
                continue
            parent_id = payment_span.get("attributes", {}).get("parent_span_id")
            visited: set[str] = set()
            while parent_id and parent_id not in visited:
                visited.add(parent_id)
                parent = trace_spans_by_id.get((trace_id, parent_id))
                if not parent:
                    break
                parent_attrs = parent.get("attributes", {})
                if parent.get("service") == "orders" and parent_attrs.get("operation") == "orders/orders":
                    link = {"type": "orders_to_payment_trace_path", "evidence": [_ref(log), _ref(payment_span)]}
                    if link not in findings["payment_dependency_timeout"]["causal_links"]:
                        findings["payment_dependency_timeout"]["causal_links"].append(link)
                    break
                parent_id = parent_attrs.get("parent_span_id")
    timeout_facts_exist = bool(payment_timeout_logs and payment_timeout_spans)
    timeout_causal_link_exists = bool(findings["payment_dependency_timeout"]["causal_links"])

    logs = source_results.get("logs", {}).get("records", [])
    traces = source_results.get("traces", {}).get("records", [])
    missing_trace_logs = [row for row in logs if not row.get("trace_id")]
    trace_ids = {row.get("trace_id") for row in traces if row.get("trace_id")}
    request_delta = _counter_delta(_metric_records(records, "requests_total"))
    trace_coverage = min(1.0, len(trace_ids) / request_delta) if request_delta > 0 else None

    spans_by_trace_and_id = {(row.get("trace_id"), row.get("attributes", {}).get("span_id")): row
        for row in traces if row.get("trace_id") and row.get("attributes", {}).get("span_id")}
    dependency_edges: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for child in traces:
        attrs = child.get("attributes", {})
        parent = spans_by_trace_and_id.get((child.get("trace_id"), attrs.get("parent_span_id")))
        if not parent:
            continue
        caller, callee = parent.get("service"), child.get("service")
        if parent and caller and callee and caller != callee:
            key = (caller, callee)
            if _ref(child) not in dependency_edges[key]:
                dependency_edges[key].append(_ref(child))

    same_trace_skews = []
    log_times: dict[str, list[datetime]] = defaultdict(list)
    span_times: dict[str, list[datetime]] = defaultdict(list)
    for row in logs:
        if row.get("trace_id"):
            log_times[row["trace_id"]].append(_instant(row["timestamp"]))
    for row in traces:
        if row.get("trace_id"):
            span_times[row["trace_id"]].append(_instant(row["timestamp"]))
    for trace_id in log_times.keys() & span_times.keys():
        skew = abs((min(log_times[trace_id]) - min(span_times[trace_id])).total_seconds())
        if skew > 2.0:
            same_trace_skews.append({"trace_id": trace_id, "seconds": round(skew, 3)})

    hypotheses = []
    for cause, _title in CAUSES:
        item = findings[cause]
        source_count = len(item["source_set"])
        score = min(1.0, 0.35 * len(item["source_set"]) + 0.08 * min(len(item["support"]), 4) - 0.25 * bool(item["against"]))
        causal_gate = cause != "payment_dependency_timeout" or bool(item["causal_links"])
        item["confidence"] = "high" if source_count >= 2 and len(item["support"]) >= 2 and not item["against"] and causal_gate else "medium" if item["support"] else "low"
        if cause == "payment_dependency_timeout":
            if item["causal_links"]:
                item["evidence_relationship"] = "causal_trace_link"
            elif source_count >= 2:
                item["evidence_relationship"] = "cross_source_correlation_only"
            else:
                item["evidence_relationship"] = "single_source_signal" if item["support"] else "no_support"
        item["score"] = round(max(0.0, score), 2)
        item["independent_sources"] = sorted(item.pop("source_set"))
        hypotheses.append(item)
    hypotheses.sort(key=lambda item: (-item["score"], item["id"]))
    claimed = [item["id"] for item in hypotheses if item["confidence"] == "high" and item["score"] >= 0.55]
    sufficient = bool(claimed)

    warnings = []
    if failures or partial_failures:
        warnings.append("one_or_more_sources_unavailable")
    if missing_trace_logs or (request_delta > 0 and not trace_ids):
        warnings.append("missing_trace_context")
    if trace_coverage is not None and trace_coverage < 0.8:
        warnings.append("possible_trace_sampling_bias")
    deployment_result = source_results.get("deployments", {})
    if "source_order_out_of_order" in deployment_result.get("warnings", []):
        warnings.append("deployment_events_out_of_order")
    if same_trace_skews:
        warnings.append("source_clock_drift")
    if timeout_facts_exist and not timeout_causal_link_exists:
        warnings.append("unlinked_cross_source_signals")

    source_details = {}
    for source in ("metrics", "logs", "traces", "deployments"):
        result = source_results.get(source, {})
        source_records = result.get("records", [])
        status = source_status.get(source, "error")
        source_details[source] = {
            "status": status,
            "window": {"start": start, "end": end},
            "query": result.get("query", {"start": start, "end": end, "limit": limit}),
            "record_count": len(source_records) if isinstance(source_records, list) else 0,
            "evidence_refs": [_ref(row) for row in source_records if isinstance(row, dict) and row.get("record_id")],
            "warnings": result.get("warnings", []),
            "failed_services": result.get("failed_services", {}),
            "service_status": result.get("service_status", {}),
            "error": failures.get(source),
            "reason": "no_matching_records" if status == "empty" else "adapter_unavailable" if status == "error" else None,
        }

    return {
        "run_id": str(uuid.uuid4()), "window": {"start": start, "end": end},
        "status": "concluded" if sufficient else "insufficient_evidence",
        "root_causes": claimed, "confidence": "high" if sufficient else "abstain",
        "hypotheses": hypotheses, "source_status": source_status, "sources": source_details,
        "evidence_refs": [{"source": row["source"], "record_id": row["record_id"]} for row in records],
        "dependency_graph": [{"caller": caller, "callee": callee, "evidence": refs}
            for (caller, callee), refs in sorted(dependency_edges.items())],
        "counter_evidence": [{"hypothesis_id": row["id"], **against_item}
            for row in hypotheses for against_item in row["against"]],
        "quality": {"missing_trace_log_records": len(missing_trace_logs), "trace_coverage_ratio_approx": trace_coverage,
            "same_trace_clock_skews": same_trace_skews, "warnings": list(dict.fromkeys(warnings))},
        "failures": failures, "partial_failures": partial_failures,
    }
