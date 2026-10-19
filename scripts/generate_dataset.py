"""Generate deterministic synthetic OpsPilot S0 incidents."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261019
BASE = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def record(record_id: str, at: datetime, service: str, **attributes: object) -> dict:
    return {
        "record_id": record_id,
        "timestamp": stamp(at),
        "service": service,
        "attributes": attributes,
    }


def build_case(rng: random.Random, incident_id: str, kind: str, offset: int) -> tuple[dict, dict]:
    start = BASE + timedelta(days=offset)
    alert_at = start + timedelta(minutes=5)
    end = start + timedelta(minutes=15)
    trace_id = f"tr-{incident_id}-a91c"
    metrics: list[dict] = []
    logs: list[dict] = []
    traces: list[dict] = []
    deployments: list[dict] = []
    timeline: list[dict] = []

    def add_log(record_id: str, at: datetime, service: str, message: str, **attrs: object) -> None:
        row = record(record_id, at, service, **attrs)
        row["message"] = message
        logs.append(row)

    if kind == "pool_exhaustion":
        limit = 40
        in_use = rng.randint(38, 40)
        wait_ms = rng.randint(1800, 2600)
        metrics.extend([
            record(f"{incident_id}-m1", alert_at, "order", name="db_pool_in_use", value=in_use, unit="connections", limit=limit),
            record(f"{incident_id}-m2", alert_at, "order", name="db_pool_wait_ms", value=wait_ms, unit="ms"),
            record(f"{incident_id}-m3", alert_at, "gateway", name="http_5xx_rate", value=0.27, unit="ratio"),
            record(f"{incident_id}-m4", alert_at, "database", name="database_health", value="healthy"),
        ])
        add_log(f"{incident_id}-l1", alert_at, "order", "timed out waiting for database connection", pool_limit=limit)
        traces.append(record(f"{incident_id}-t1", alert_at, "order", trace_id=trace_id, span="db.connection.acquire", duration_ms=wait_ms, status="error"))
        timeline.append({"timestamp": stamp(alert_at), "event": "5xx alert; database pool wait elevated"})
        diagnosis = "database connection pool exhausted"
        category = "pool_exhaustion"
    elif kind == "config_error":
        version = f"order-v{rng.randint(12, 19)}"
        deploy_at = start + timedelta(minutes=2)
        deployments.append(record(f"{incident_id}-d1", deploy_at, "order", version=version, change="config bundle", actor="synthetic-release"))
        metrics.extend([
            record(f"{incident_id}-m1", alert_at, "order", name="http_5xx_rate", value=0.34, unit="ratio", version=version),
            record(f"{incident_id}-m2", alert_at, "order", name="config_validation_errors", value=rng.randint(7, 12), unit="count", version=version),
            record(f"{incident_id}-m3", alert_at, "database", name="database_health", value="healthy"),
        ])
        add_log(f"{incident_id}-l1", alert_at, "order", "required downstream endpoint missing from active configuration", version=version, error="CONFIG_ENDPOINT_MISSING")
        traces.append(record(f"{incident_id}-t1", alert_at, "order", trace_id=trace_id, span="config.validate", duration_ms=4, status="error", version=version))
        timeline.extend([
            {"timestamp": stamp(deploy_at), "event": f"deployed {version}"},
            {"timestamp": stamp(alert_at), "event": "5xx alert; config validation failures"},
        ])
        diagnosis = "deployment introduced invalid order service configuration"
        category = "config_error"
    elif kind == "downstream_timeout":
        latency = rng.randint(2200, 3100)
        metrics.extend([
            record(f"{incident_id}-m1", alert_at, "downstream", name="request_duration_ms_p95", value=latency, unit="ms"),
            record(f"{incident_id}-m2", alert_at, "order", name="downstream_timeout_rate", value=0.31, unit="ratio"),
            record(f"{incident_id}-m3", alert_at, "order", name="db_pool_in_use", value=rng.randint(9, 15), unit="connections", limit=40),
            record(f"{incident_id}-m4", alert_at, "gateway", name="http_5xx_rate", value=0.24, unit="ratio"),
        ])
        add_log(f"{incident_id}-l1", alert_at, "order", "downstream request exceeded deadline", trace_id=trace_id, timeout_ms=2000)
        # Deliberately noisy clue for later S2 counter-evidence exercises.
        add_log(f"{incident_id}-l2", alert_at, "order", "database pool warning: transient queue observed", severity="warning")
        traces.extend([
            record(f"{incident_id}-t1", alert_at, "order", trace_id=trace_id, span="order.downstream.call", duration_ms=latency, status="error", peer_service="downstream"),
            record(f"{incident_id}-t2", alert_at, "gateway", trace_id=trace_id, span="gateway.order.request", duration_ms=latency + 18, status="error", peer_service="order"),
        ])
        timeline.append({"timestamp": stamp(alert_at), "event": "5xx alert; downstream latency and timeout rate elevated"})
        diagnosis = "downstream service exceeded request timeout"
        category = "downstream_timeout"
    elif incident_id == "unknown-01":
        metrics.append(record(f"{incident_id}-m1", alert_at, "gateway", name="http_5xx_rate", value=0.22, unit="ratio"))
        add_log(f"{incident_id}-l1", alert_at, "gateway", "request failed", severity="error")
        timeline.append({"timestamp": stamp(alert_at), "event": "5xx alert; source signals incomplete"})
        diagnosis = None
        category = "insufficient_evidence"
    else:
        metrics.extend([
            record(f"{incident_id}-m1", alert_at, "order", name="db_pool_in_use", value=39, unit="connections", limit=40),
            record(f"{incident_id}-m2", alert_at, "downstream", name="request_duration_ms_p95", value=2800, unit="ms"),
            record(f"{incident_id}-m3", alert_at, "gateway", name="http_5xx_rate", value=0.25, unit="ratio"),
        ])
        traces.append(record(f"{incident_id}-t1", alert_at, "order", trace_id=trace_id, span="order.request", duration_ms=None, status="missing_child_spans"))
        add_log(f"{incident_id}-l1", alert_at, "order", "request timeout", severity="error")
        timeline.append({"timestamp": stamp(alert_at), "event": "5xx alert; metrics conflict and trace incomplete"})
        diagnosis = None
        category = "insufficient_evidence"

    incident = {
        "incident_id": incident_id,
        "service": "gateway",
        "window": {"start": stamp(start), "end": stamp(end)},
        "alert": {"name": "http_5xx_rate", "observed_value": 0.2, "unit": "ratio", "timestamp": stamp(alert_at)},
        "timeline": sorted(timeline, key=lambda item: item["timestamp"]),
        "signals": {"metrics": metrics, "logs": logs, "traces": traces, "deployments": deployments},
    }
    label = {
        "incident_id": incident_id,
        "category": category,
        "root_cause": diagnosis,
        "decidable": diagnosis is not None,
    }
    return incident, label


def validate(incidents: list[dict], labels: list[dict]) -> None:
    expected = {"pool-01", "pool-02", "config-01", "config-02", "downstream-01", "downstream-02", "unknown-01", "unknown-02"}
    ids = [item["incident_id"] for item in incidents]
    if set(ids) != expected or len(ids) != len(expected):
        raise ValueError("incident IDs do not match the fixed 8-case set")
    if {item["incident_id"] for item in labels} != expected:
        raise ValueError("label IDs do not match incident IDs")
    if sum(item["decidable"] for item in labels) != 6:
        raise ValueError("expected 6 decidable cases")
    for incident in incidents:
        if set(incident["signals"]) != {"metrics", "logs", "traces", "deployments"}:
            raise ValueError(f"signal source missing for {incident['incident_id']}")
        for rows in incident["signals"].values():
            for row in rows:
                if not row.get("record_id") or not row.get("timestamp"):
                    raise ValueError(f"record missing ID or timestamp in {incident['incident_id']}")


def main() -> None:
    rng = random.Random(SEED)
    definitions = [
        ("pool-01", "pool_exhaustion", 0), ("pool-02", "pool_exhaustion", 1),
        ("config-01", "config_error", 2), ("config-02", "config_error", 3),
        ("downstream-01", "downstream_timeout", 4), ("downstream-02", "downstream_timeout", 5),
        ("unknown-01", "insufficient_evidence", 6), ("unknown-02", "insufficient_evidence", 7),
    ]
    pairs = [build_case(rng, *definition) for definition in definitions]
    incidents, labels = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    validate(incidents, labels)

    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    incident_path = data_dir / "incidents.jsonl"
    label_path = data_dir / "labels.json"
    incident_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in incidents), encoding="utf-8", newline="\n")
    label_path.write_text(json.dumps({"seed": SEED, "labels": labels}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    digest = hashlib.sha256(incident_path.read_bytes()).hexdigest()
    print(f"generated={len(incidents)} decidable=6 insufficient=2 seed={SEED}")
    print(f"incidents_sha256={digest}")


if __name__ == "__main__":
    main()
