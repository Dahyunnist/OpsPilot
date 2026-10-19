"""Read-only signal queries and deterministic hypothesis ledger."""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any

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


def query_source(incident: dict[str, Any], source: str, max_rows: int = 100, missing: bool = False) -> dict[str, Any]:
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
    if missing:
        rows: list[dict[str, Any]] = []
        status = "missing"
    else:
        rows = [row for row in incident["signals"][source] if _inside_window(row, window["start"], window["end"])][:max_rows]
        status = "ok"
    return {
        "source": source,
        "params": params,
        "status": status,
        "record_count": len(rows),
        "records": rows,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }


def _record_text(row: dict[str, Any]) -> str:
    return (row.get("message", "") + " " + str(row.get("attributes", {}))).lower()


def _ledger(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [record for query in queries for record in query["records"]]
    scores = {key: {"title": title, "support": [], "against": [], "open_questions": []} for key, title in HYPOTHESES}
    source_status = {query["source"]: query["status"] for query in queries}

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
        if "db_pool_wait_ms" in str(attrs) or "waiting for database connection" in text or "db.connection.acquire" in text:
            scores["pool_exhaustion"]["support"].append(record_id)
        if "config" in text or "config_validation_errors" in str(attrs):
            scores["config_error"]["support"].append(record_id)
        if row.get("service") == "order" and row.get("attributes", {}).get("version"):
            version = row["attributes"]["version"]
            if any(d.get("attributes", {}).get("version") == version for d in queries[3]["records"]):
                scores["config_error"]["support"].append(record_id)
        if "downstream request" in row.get("message", "").lower() or attrs.get("peer_service") == "downstream":
            scores["downstream_timeout"]["support"].append(record_id)
        if attrs.get("name") == "request_duration_ms_p95" and attrs.get("value", 0) > 2000:
            scores["downstream_timeout"]["support"].append(record_id)

    for hypothesis in scores.values():
        for source in SOURCES:
            if source_status[source] == "missing":
                hypothesis["open_questions"].append(f"{source} source unavailable")
    result = []
    for key, definition in HYPOTHESES:
        item = scores[key]
        item["support"] = sorted(set(item["support"]))
        item["against"] = sorted(set(item["against"]))
        item["score"] = len(item["support"]) - len(item["against"])
        item["hypothesis_id"] = key
        item["title"] = definition
        result.append(item)
    return sorted(result, key=lambda item: (-item["score"], item["hypothesis_id"]))


def investigate(incident: dict[str, Any], missing_sources: set[str] | None = None, max_rows: int = 100) -> dict[str, Any]:
    missing_sources = missing_sources or set()
    started = perf_counter()
    queries = [query_source(incident, source, max_rows=max_rows, missing=source in missing_sources) for source in SOURCES]
    hypotheses = _ledger(queries)
    top = hypotheses[0]
    second_score = hypotheses[1]["score"]
    decisive = len(top["support"]) >= 2 and top["score"] > second_score
    conclusion = top["title"] if decisive else None
    status = "determined" if decisive else "insufficient_evidence"
    return {
        "incident_id": incident["incident_id"],
        "status": status,
        "conclusion": conclusion,
        "hypotheses": hypotheses,
        "query_trace": queries,
        "cost": {
            "query_count": len(queries),
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "input_tokens": None,
            "output_tokens": None,
        },
    }

