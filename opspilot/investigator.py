"""Read-only signal queries and deterministic hypothesis ledger."""

from __future__ import annotations

from datetime import datetime
import json
from time import perf_counter
from typing import Any
from pathlib import Path
import hashlib

SOURCES = ("metrics", "logs", "traces", "deployments")
HYPOTHESES = (
    ("pool_exhaustion", "database connection pool exhausted"),
    ("config_error", "deployment introduced invalid order service configuration"),
    ("downstream_timeout", "downstream service exceeded request timeout"),
)


def _inside_window(record: dict[str, Any], start: str, end: str) -> bool:
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        return False
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        lower = datetime.fromisoformat(start.replace("Z", "+00:00"))
        upper = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return False
    return lower <= instant <= upper


def query_source(incident: dict[str, Any], source: str, max_rows: int = 100, missing: bool = False, fault: str | None = None) -> dict[str, Any]:
    if source not in SOURCES:
        raise ValueError(f"unsupported source: {source}")
    if not 1 <= max_rows <= 1000:
        raise ValueError("max_rows must be between 1 and 1000")
    window = incident["window"]
    params = {
        "incident_id": incident["incident_id"],
        "start": window["start"],
        "end": window["end"],
        "limit": max_rows,
    }
    started = perf_counter()
    duplicate_count = 0
    out_of_order = False
    if missing:
        rows: list[dict[str, Any]] = []
        status = "missing"
    elif fault == "timeout":
        rows = []
        status = "timeout"
    else:
        rows = [row for row in incident["signals"][source] if _inside_window(row, window["start"], window["end"])][:max_rows]
        status = "ok"
        if fault == "duplicate" and rows:
            rows = rows + [rows[0]]
        if fault == "out_of_order":
            rows.reverse()
            out_of_order = rows != sorted(rows, key=lambda row: (row["timestamp"], row["record_id"]))
        before_dedupe = len(rows)
        rows = list({row["record_id"]: row for row in rows}.values())
        duplicate_count = before_dedupe - len(rows)
        rows.sort(key=lambda row: (row["timestamp"], row["record_id"]))
    return {
        "source": source,
        "params": params,
        "status": status,
        "record_count": len(rows),
        "records": rows,
        "fault_injected": fault,
        "duplicates_dropped": duplicate_count,
        "source_was_out_of_order": out_of_order,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }


def _record_text(row: dict[str, Any]) -> str:
    return (row.get("message", "") + " " + str(row.get("attributes", {}))).lower()


def _query_map(queries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {query["source"]: query for query in queries}


def _ledger(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [record for query in queries for record in query["records"]]
    scores = {key: {"title": title, "support": [], "against": [], "open_questions": []} for key, title in HYPOTHESES}
    source_status = {source: query.get("status", "not_queried") for source, query in _query_map(queries).items()}
    deployments = _query_map(queries).get("deployments", {}).get("records", [])

    for row in rows:
        record_id = row["record_id"]
        text = _record_text(row)
        attrs = row.get("attributes", {})
        if attrs.get("name") == "db_pool_in_use" and attrs.get("limit"):
            ratio = attrs["value"] / attrs["limit"]
            if ratio >= 0.9:
                scores["pool_exhaustion"]["support"].append(record_id)
            elif ratio <= 0.5:
                scores["pool_exhaustion"]["against"].append(record_id)
        if "database pool warning" in row.get("message", "").lower():
            scores["pool_exhaustion"]["support"].append(record_id)
        if "db_pool_wait_ms" in str(attrs) or "waiting for database connection" in text or "db.connection.acquire" in text:
            scores["pool_exhaustion"]["support"].append(record_id)
        if "config" in text or "config_validation_errors" in str(attrs):
            scores["config_error"]["support"].append(record_id)
        if row.get("service") == "order" and row.get("attributes", {}).get("version"):
            version = row["attributes"]["version"]
            if any(d.get("attributes", {}).get("version") == version for d in deployments):
                scores["config_error"]["support"].append(record_id)
        if "downstream request" in row.get("message", "").lower() or attrs.get("peer_service") == "downstream":
            scores["downstream_timeout"]["support"].append(record_id)
        if attrs.get("span") == "order.downstream.call":
            scores["pool_exhaustion"]["against"].append(record_id)
        if attrs.get("name") == "request_duration_ms_p95" and attrs.get("value", 0) > 2000:
            scores["downstream_timeout"]["support"].append(record_id)

    for hypothesis in scores.values():
        for source in SOURCES:
            if source_status.get(source) in {"missing", "timeout"}:
                hypothesis["open_questions"].append(f"{source} source {source_status[source]}")
            elif source_status.get(source, "not_queried") == "not_queried":
                hypothesis["open_questions"].append(f"{source} source not queried")
    result = []
    for key, definition in HYPOTHESES:
        item = scores[key]
        item["support"] = sorted(set(item["support"]))
        item["against"] = sorted(set(item["against"]))
        item["score"] = len(item["support"]) - len(item["against"])
        item["hypothesis_id"] = key
        item["title"] = definition
        result.append(item)
    return sorted(result, key=lambda item: -item["score"])


def _strong_support(queries: list[dict[str, Any]]) -> dict[str, list[str]]:
    records = [(query["source"], row) for query in queries for row in query.get("records", [])]
    strong = {key: set() for key, _ in HYPOTHESES}
    config_versions = set()
    for source, row in records:
        attrs = row.get("attributes", {})
        text = row.get("message", "").lower()
        if source == "metrics" and attrs.get("name") == "db_pool_in_use" and attrs.get("limit") and attrs.get("value", 0) / attrs["limit"] >= 0.9:
            strong["pool_exhaustion"].add(row["record_id"])
        if source == "metrics" and attrs.get("name") == "db_pool_wait_ms" and attrs.get("value", 0) >= 100:
            strong["pool_exhaustion"].add(row["record_id"])
        if source == "logs" and "waiting for database connection" in text:
            strong["pool_exhaustion"].add(row["record_id"])
        if source == "traces" and attrs.get("span") == "db.connection.acquire" and attrs.get("status") == "error":
            strong["pool_exhaustion"].add(row["record_id"])

        if source == "metrics" and attrs.get("name") == "config_validation_errors" and attrs.get("value", 0) > 0:
            strong["config_error"].add(row["record_id"])
            if attrs.get("version"):
                config_versions.add(attrs["version"])
        if source == "logs" and ("required downstream endpoint missing" in text or attrs.get("error") == "CONFIG_ENDPOINT_MISSING"):
            strong["config_error"].add(row["record_id"])
            if attrs.get("version"):
                config_versions.add(attrs["version"])
        if source == "traces" and attrs.get("span") == "config.validate" and attrs.get("status") == "error":
            strong["config_error"].add(row["record_id"])
            if attrs.get("version"):
                config_versions.add(attrs["version"])
        if source == "deployments" and attrs.get("version") in config_versions:
            strong["config_error"].add(row["record_id"])

        if source == "metrics" and attrs.get("name") in {"request_duration_ms_p95", "downstream_timeout_rate"} and attrs.get("value", 0) > 0:
            strong["downstream_timeout"].add(row["record_id"])
        if source == "logs" and "downstream request exceeded" in text:
            strong["downstream_timeout"].add(row["record_id"])
        if source == "traces" and attrs.get("span") == "order.downstream.call" and attrs.get("status") == "error":
            strong["downstream_timeout"].add(row["record_id"])
    # Link deployments found before their associated validation metric in record order.
    for _source, row in records:
        if _source == "deployments" and row.get("attributes", {}).get("version") in config_versions:
            strong["config_error"].add(row["record_id"])
    return {key: sorted(ids) for key, ids in strong.items()}


def _choose_followups(metrics: list[dict[str, Any]]) -> tuple[list[str], str]:
    names = {row.get("attributes", {}).get("name") for row in metrics}
    if "config_validation_errors" in names:
        return ["deployments", "logs", "traces"], "configuration metric makes deployment/version and error records most discriminating"
    if "db_pool_in_use" in names:
        return ["traces", "logs", "deployments"], "pool utilization needs trace and log confirmation; deployment helps test a competing cause"
    if "request_duration_ms_p95" in names or "downstream_timeout_rate" in names:
        return ["traces", "logs", "deployments"], "downstream latency needs trace localization and matching timeout logs"
    return ["logs", "traces", "deployments"], "symptom metrics alone do not distinguish the candidate causes"


def _correlations(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for query in queries:
        for row in query["records"]:
            attrs = row.get("attributes", {})
            for kind, value in (("trace_id", attrs.get("trace_id")), ("version", attrs.get("version"))):
                if value:
                    grouped.setdefault((kind, str(value)), []).append(row["record_id"])
    return [
        {"kind": kind, "key": value, "record_ids": sorted(set(ids)), "sources": sorted({query["source"] for query in queries for row in query["records"] if row["record_id"] in ids})}
        for (kind, value), ids in sorted(grouped.items())
        if len(set(ids)) > 1
    ]


def _load_state(path: Path, incident: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return {"queries": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("incident_id") != incident["incident_id"] or state.get("window") != incident["window"]:
        raise ValueError("state file belongs to a different incident or time window")
    return state


def _save_state(path: Path, incident: dict[str, Any], queries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"incident_id": incident["incident_id"], "window": incident["window"], "queries": queries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _recommendation(conclusion: str | None) -> dict[str, Any] | None:
    actions = {
        "database connection pool exhausted": ("restart_order", "high", "Restore service capacity and verify connections after restart."),
        "deployment introduced invalid order service configuration": ("rollback_order_config", "high", "Redeploy the last known-good configuration after approval."),
        "downstream service exceeded request timeout": ("restart_downstream", "high", "Restore the downstream service and recheck dependent callers."),
    }
    if conclusion not in actions:
        return None
    action_id, risk, rollback = actions[conclusion]
    return {"action_id": action_id, "risk": risk, "requires_approval": True, "execution": "simulation_only", "rollback": rollback}


def investigate(
    incident: dict[str, Any],
    missing_sources: set[str] | None = None,
    max_rows: int = 100,
    query_budget: int = 8,
    fault_injections: dict[str, str] | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    missing_sources = missing_sources or set()
    fault_injections = fault_injections or {}
    if not 1 <= query_budget <= 12:
        raise ValueError("query_budget must be between 1 and 12")
    started = perf_counter()
    state = _load_state(state_path, incident) if state_path else {"queries": []}
    queries = list(state.get("queries", []))
    cached = {query["source"]: query for query in queries if query["status"] != "timeout"}
    attempted = 0

    def run_source(source: str) -> dict[str, Any] | None:
        nonlocal attempted
        if source in cached:
            cached[source]["fresh"] = False
            return cached[source]
        if attempted >= query_budget:
            return None
        attempted += 1
        result = query_source(incident, source, max_rows=max_rows, missing=source in missing_sources, fault=fault_injections.get(source))
        result["fresh"] = True
        queries.append(result)
        if state_path:
            _save_state(state_path, incident, queries)
        return result

    metrics = run_source("metrics")
    planned_followups, strategy_reason = _choose_followups(metrics["records"] if metrics else [])
    for source in planned_followups:
        run_source(source)
    queries.sort(key=lambda query: (SOURCES.index(query["source"]), query["params"]["start"]))
    hypotheses = _ledger(queries)
    strong_ids = _strong_support(queries)
    for hypothesis in hypotheses:
        hypothesis["strong_support"] = strong_ids[hypothesis["hypothesis_id"]]
    queried_sources = {query["source"] for query in queries}
    top = hypotheses[0]
    second_score = hypotheses[1]["score"]
    strong_sources = {query["source"] for query in queries if any(row["record_id"] in top["strong_support"] for row in query["records"])}
    decisive = (
        all(source in queried_sources for source in planned_followups)
        and len(top["strong_support"]) >= 2
        and len(strong_sources) >= 2
        and top["score"] > second_score
    )
    conclusion = top["title"] if decisive else None
    status = "determined" if decisive else "insufficient_evidence"
    log_query = next((query for query in queries if query["source"] == "logs"), None)
    log_hypotheses = _ledger([log_query]) if log_query else []
    log_top = log_hypotheses[0] if log_hypotheses else None
    log_conclusion = log_top["title"] if log_top and log_top["support"] else None
    no_refutation_ranked = sorted(hypotheses, key=lambda item: -len(item["support"]))
    no_refutation_top = no_refutation_ranked[0]
    no_refutation_second = len(no_refutation_ranked[1]["support"])
    no_refutation_conclusion = (
        no_refutation_top["title"]
        if len(no_refutation_top["support"]) >= 2 and len(no_refutation_top["support"]) > no_refutation_second
        else None
    )
    rejected = [
        {"hypothesis_id": item["hypothesis_id"], "title": item["title"], "counter_evidence": item["against"]}
        for item in hypotheses
        if item["against"] and (item["hypothesis_id"] != top["hypothesis_id"] or item["score"] < 0)
    ]
    evidence_ids = {row["record_id"] for query in queries for row in query["records"]}
    citations_valid = all(
        evidence_id in evidence_ids
        for item in hypotheses
        for evidence_id in item["support"] + item["against"]
    )
    for item in hypotheses:
        item["disposition"] = "leading" if conclusion and item["title"] == conclusion else ("rejected" if item["against"] and item["score"] < 0 else "open")
    return {
        "incident_id": incident["incident_id"],
        "status": status,
        "conclusion": conclusion,
        "hypotheses": hypotheses,
        "query_strategy": {"first": "metrics", "followups": planned_followups, "reason": strategy_reason},
        "comparison": {
            "logs_only": {"status": "tentative" if log_conclusion else "insufficient_evidence", "conclusion": log_conclusion},
            "no_refutation": {
                "status": "determined" if no_refutation_conclusion else "insufficient_evidence",
                "conclusion": no_refutation_conclusion,
                "supported_competitors": [
                    item["hypothesis_id"] for item in no_refutation_ranked
                    if item["support"] and item["title"] != no_refutation_conclusion
                ],
            },
            "multi_signal": {"status": status, "conclusion": conclusion},
        },
        "rejected_hypotheses": rejected,
        "correlations": _correlations(queries),
        "citation_validation": {"all_references_exist": citations_valid, "evidence_record_count": len(evidence_ids)},
        "query_trace": queries,
        "cost": {
            "query_count": attempted,
            "reused_query_count": len(queries) - attempted,
            "query_budget": query_budget,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "input_tokens": None,
            "output_tokens": None,
        },
        "recommendation": _recommendation(conclusion),
    }
