"""Generate separately frozen, synthetic cross-source conflict benchmark."""

from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opspilot.cli import load_incidents  # noqa: E402

SEED = 20261029
SPLITS = {"train": 8, "validation": 4, "test": 4}
TEMPLATES = {
    "pool_exhaustion": "pool-01",
    "config_error": "config-01",
    "downstream_timeout": "downstream-02",
    "insufficient_evidence": "unknown-01",
}
ROOT_CAUSES = {
    "pool_exhaustion": "database connection pool exhausted",
    "config_error": "deployment introduced invalid order service configuration",
    "downstream_timeout": "downstream service exceeded request timeout",
    "insufficient_evidence": None,
}


def _retag(case: dict, incident_id: str) -> None:
    case["incident_id"] = incident_id
    case["window"] = {"start": "2027-02-01T09:00:00Z", "end": "2027-02-01T09:15:00Z"}
    case["alert"]["timestamp"] = "2027-02-01T09:05:00Z"
    case["timeline"] = [{"event": "synthetic 5xx incident; source-level clues conflict", "timestamp": "2027-02-01T09:05:00Z"}]
    for source, rows in case["signals"].items():
        for index, row in enumerate(rows, 1):
            row["record_id"] = f"{incident_id}-{source[:1]}{index}"
            row["timestamp"] = f"2027-02-01T09:{4 + index:02d}:00Z"
            attrs = row.get("attributes", {})
            if "trace_id" in attrs:
                attrs["trace_id"] = f"trace-{incident_id}-causal"
            if "version" in attrs and attrs["version"]:
                attrs["version"] = f"order-{incident_id}"


def _make_case(base: dict, incident_id: str, category: str, variant: int, rng: random.Random) -> tuple[dict, list[str], list[str]]:
    case = copy.deepcopy(base)
    _retag(case, incident_id)
    signals = case["signals"]
    root_ids = []
    causal_trace = f"trace-{incident_id}-causal"
    decoy_trace = f"trace-{incident_id}-decoy"

    # Every decidable case contains competing pool and configuration clues.
    # Only trace/service/version joins distinguish cause from co-occurrence.
    signals["logs"].append({
        "record_id": f"{incident_id}-l-decoy", "timestamp": "2027-02-01T09:06:00Z",
        "service": "catalog-worker", "message": "database pool warning: transient queue observed; no customer request linked",
        "attributes": {"severity": "warning", "trace_id": decoy_trace, "service.name": "catalog-worker"},
    })
    signals["metrics"].append({
        "record_id": f"{incident_id}-m-decoy", "timestamp": "2027-02-01T09:06:00Z",
        "service": "catalog-worker", "attributes": {"name": "db_pool_in_use", "value": 39 if category not in {"pool_exhaustion", "insufficient_evidence"} else 8,
        "limit": 40, "trace_id": decoy_trace, "service.name": "catalog-worker"},
    })
    signals["traces"].append({
        "record_id": f"{incident_id}-t-decoy", "timestamp": "2027-02-01T09:06:00Z",
        "service": "catalog-worker", "attributes": {"trace_id": decoy_trace, "span": "db.connection.acquire",
        "status": "error" if category not in {"pool_exhaustion", "insufficient_evidence"} else "ok", "duration_ms": 4000,
        "service.name": "catalog-worker", "service.version": "worker-v8", "peer.service": "database"},
    })

    for source, rows in signals.items():
        for row in rows:
            attrs = row.get("attributes", {})
            text = row.get("message", "").lower()
            if category == "pool_exhaustion" and (
                attrs.get("name") in {"db_pool_in_use", "db_pool_wait_ms"}
                or "waiting for database connection" in text
                or attrs.get("span") == "db.connection.acquire"
            ) and row.get("service") != "catalog-worker":
                root_ids.append(row["record_id"])
            if category == "config_error" and (
                attrs.get("name") == "config_validation_errors"
                or "configuration" in text
                or attrs.get("span") == "config.validate"
                or source == "deployments"
            ):
                root_ids.append(row["record_id"])
            if category == "downstream_timeout" and (
                attrs.get("name") in {"request_duration_ms_p95", "downstream_timeout_rate"}
                or "downstream request" in text
                or attrs.get("span") == "order.downstream.call"
            ):
                root_ids.append(row["record_id"])

    if category == "pool_exhaustion":
        # Cross-service parent span joins order->database acquisition to gateway request.
        signals["traces"].append({"record_id": f"{incident_id}-t-parent", "timestamp": "2027-02-01T09:07:00Z",
            "service": "gateway", "attributes": {"trace_id": causal_trace, "span": "gateway.request", "status": "error",
            "duration_ms": 3200, "service.name": "gateway", "service.version": "gw-v3", "peer.service": "order"}})
        root_ids += [f"{incident_id}-t-parent"]
    elif category == "config_error":
        # Real release is tied to order service.version; a recent cart rollout is distractor.
        signals["deployments"].append({"record_id": f"{incident_id}-d-decoy", "timestamp": "2027-02-01T09:03:00Z",
            "service": "catalog-worker", "attributes": {"version": "worker-v8", "service.name": "catalog-worker", "change": "routine release"}})
    elif category == "downstream_timeout":
        signals["traces"].append({"record_id": f"{incident_id}-t-parent", "timestamp": "2027-02-01T09:07:00Z",
            "service": "gateway", "attributes": {"trace_id": causal_trace, "span": "gateway.request", "status": "error",
            "duration_ms": 4000, "service.name": "gateway", "service.version": "gw-v3", "peer.service": "order"}})
        root_ids += [f"{incident_id}-t-parent"]

    if category == "insufficient_evidence":
        # Preserve three plausible causes, but remove the causal bridge and critical sources.
        signals["traces"] = [row for row in signals["traces"] if row["record_id"].endswith("decoy")]
        signals["deployments"] = []
        signals["logs"] = [row for row in signals["logs"] if row["record_id"].endswith("decoy")]
        signals["metrics"] = [row for row in signals["metrics"] if row["record_id"].endswith("decoy")]
        root_ids = []
    else:
        # Add linkage fields to genuine cause evidence, preserving decoy's different identity.
        for rows in signals.values():
            for row in rows:
                if row["record_id"] in root_ids:
                    attrs = row.setdefault("attributes", {})
                    attrs.setdefault("trace_id", causal_trace)
                    attrs.setdefault("service.name", row.get("service", "order"))
                    if category == "config_error":
                        attrs["service.version"] = f"order-{incident_id}"
        # Causal order appears in separate telemetry and can arrive after noisy warnings.
        for rows in signals.values():
            rng.shuffle(rows)
        if variant % 3 == 0:
            missing = set(root_ids)
            for source in signals:
                signals[source] = [row for row in signals[source] if row["record_id"] not in missing]
            for row in signals["metrics"]:
                if row["record_id"].endswith("m-decoy"):
                    row["attributes"]["value"] = 8
            for row in signals["traces"]:
                if row["record_id"].endswith("t-decoy"):
                    row["attributes"]["status"] = "ok"
            # Remove the causal chain; remaining co-occurrence clues cannot establish cause.
            root_ids = []

    if not root_ids:
        category_truth = "insufficient_evidence"
        gold_ids: list[str] = []
    else:
        category_truth = category
        gold_ids = sorted(set(root_ids))
    return case, gold_ids, [causal_trace] if root_ids else []


def generate(output: Path = ROOT / "data" / "complex-benchmark") -> dict:
    rng = random.Random(SEED)
    base = load_incidents(ROOT / "data" / "incidents.jsonl")
    data: dict[str, list[dict]] = {name: [] for name in SPLITS}
    labels: dict[str, list[dict]] = {name: [] for name in SPLITS}
    categories = list(TEMPLATES)
    for category in categories:
        for index in range(sum(SPLITS.values())):
            split = "train" if index < SPLITS["train"] else "validation" if index < SPLITS["train"] + SPLITS["validation"] else "test"
            incident_id = f"cx-{category.replace('_', '-')}-{index + 1:02d}"
            case, gold, causal_keys = _make_case(base[TEMPLATES[category]], incident_id, category, index, rng)
            # A missing causal trace is an unanswerable case, independent of original template label.
            truth_category = category if gold else "insufficient_evidence"
            data[split].append(case)
            labels[split].append({"incident_id": incident_id, "category": truth_category,
                "root_cause": ROOT_CAUSES.get(truth_category), "decidable": bool(gold),
                "gold_evidence_ids": gold, "causal_trace_ids": causal_keys,
                "challenge": "cross_source_conflict_async_handoff"})
    manifest = {"benchmark_id": "opspilot_complex_conflicts_v1", "seed": SEED,
        "synthetic": True, "total_cases": sum(len(v) for v in data.values()), "splits": {},
        "generation_policy": "Freeze incidents and labels before any model run; labels stored outside incident inputs."}
    output.mkdir(parents=True, exist_ok=True)
    for split, rows in data.items():
        rows.sort(key=lambda row: row["incident_id"])
        labels[split].sort(key=lambda row: row["incident_id"])
        raw = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()
        gold = (json.dumps({"labels": labels[split]}, ensure_ascii=False, indent=2) + "\n").encode()
        label_dir = output / "labels"
        label_dir.mkdir(exist_ok=True)
        (output / f"{split}.jsonl").write_bytes(raw)
        (label_dir / f"{split}.json").write_bytes(gold)
        manifest["splits"][split] = {"cases": len(rows), "data_sha256": hashlib.sha256(raw).hexdigest(),
            "labels_sha256": hashlib.sha256(gold).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2))
