"""Create frozen stratified OpsPilot train/validation/blind-test data."""

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

SEED = 20261020
SPLITS = {"train": 6, "validation": 2, "test": 4}
TEMPLATES = {
    "pool_exhaustion": ("pool-01", "database connection pool exhausted"),
    "config_error": ("config-01", "deployment introduced invalid order service configuration"),
    "downstream_timeout": ("downstream-01", "downstream service exceeded request timeout"),
    "insufficient_evidence": ("unknown-02", None),
}
VARIANTS = ("noise", "contradiction", "missing_source", "cross_service", "time_skew", "clean")


def _retime(record: dict, timestamp: str) -> None:
    record["timestamp"] = timestamp


def _make_case(base: dict, incident_id: str, variant: str, rng: random.Random) -> dict:
    case = copy.deepcopy(base)
    case["incident_id"] = incident_id
    case["window"] = {"start": "2027-01-01T09:00:00Z", "end": "2027-01-01T09:15:00Z"}
    case["alert"]["timestamp"] = "2027-01-01T09:05:00Z"
    suffix = incident_id
    all_rows = []
    for source, rows in case["signals"].items():
        for index, row in enumerate(rows, 1):
            row["record_id"] = f"{suffix}-{source[:1]}{index}"
            row["timestamp"] = f"2027-01-01T09:{4 + index:02d}:00Z"
            attrs = row.get("attributes", {})
            if "trace_id" in attrs:
                attrs["trace_id"] = f"tr-{suffix}-shared"
            if "version" in attrs and attrs["version"]:
                attrs["version"] = f"order-{suffix}"
            all_rows.append(row)
    case["timeline"] = [{"event": "synthetic incident variant: " + variant, "timestamp": "2027-01-01T09:05:00Z"}]

    root = "unknown" if incident_id.startswith("unknown-") else incident_id.split("-")[0]
    if variant == "noise":
        case["signals"]["logs"].append({"record_id": f"{suffix}-noise", "timestamp": "2027-01-01T09:06:00Z", "service": "order", "message": "unrelated cache warning; request path unaffected", "attributes": {"severity": "warning"}})
    elif variant == "contradiction":
        case["signals"]["logs"].append({"record_id": f"{suffix}-conflict", "timestamp": "2027-01-01T09:07:00Z", "service": "order", "message": "database pool warning: brief transient queue observed", "attributes": {"severity": "warning"}})
        if root == "downstream":
            case["signals"]["metrics"].append({"record_id": f"{suffix}-m-conflict", "timestamp": "2027-01-01T09:07:00Z", "service": "order", "attributes": {"name": "db_pool_in_use", "value": 39, "limit": 40, "unit": "connections"}})
    elif variant == "missing_source":
        case["signals"]["deployments"] = []
    elif variant == "cross_service":
        trace_id = f"tr-{suffix}-shared"
        case["signals"]["traces"].append({"record_id": f"{suffix}-gateway-span", "timestamp": "2027-01-01T09:08:00Z", "service": "gateway", "attributes": {"trace_id": trace_id, "span": "gateway.request", "peer_service": "order", "status": "error", "duration_ms": 3000}})
    elif variant == "time_skew":
        for index, row in enumerate(case["signals"]["traces"]):
            row["timestamp"] = "2027-01-01T09:03:00Z" if index % 2 == 0 else "2027-01-01T09:10:00Z"
        rng.shuffle(case["signals"]["traces"])

    if root == "unknown":
        case["signals"]["traces"] = []
        if variant == "missing_source":
            case["signals"]["logs"] = []
        case["timeline"] = [{"event": f"synthetic incident variant: {variant}; ambiguous; critical traces unavailable", "timestamp": "2027-01-01T09:05:00Z"}]
    # Scramble storage order; query adapters must sort by timestamp and stable ID.
    for rows in case["signals"].values():
        rng.shuffle(rows)
    return case


def generate(output: Path = ROOT / "data" / "benchmark") -> dict:
    rng = random.Random(SEED)
    base = load_incidents(ROOT / "data" / "incidents.jsonl")
    generated: dict[str, list[dict]] = {name: [] for name in SPLITS}
    labels: dict[str, list[dict]] = {name: [] for name in SPLITS}
    for category, (template_id, root_cause) in TEMPLATES.items():
        entries = []
        for index in range(sum(SPLITS.values())):
            split = "train" if index < SPLITS["train"] else ("validation" if index < SPLITS["train"] + SPLITS["validation"] else "test")
            variant = VARIANTS[index % len(VARIANTS)]
            slug = "unknown" if category == "insufficient_evidence" else category.replace("_", "-")
            incident_id = f"{slug}-{index + 1:02d}"
            entries.append((split, _make_case(base[template_id], incident_id, variant, rng), root_cause))
        rng.shuffle(entries)
        # Rebalance after deterministic shuffle while retaining all six perturbation families.
        for index, (_old_split, case, _) in enumerate(entries):
            split = "train" if index < SPLITS["train"] else ("validation" if index < SPLITS["train"] + SPLITS["validation"] else "test")
            generated[split].append(case)
            labels[split].append({"incident_id": case["incident_id"], "category": category, "root_cause": root_cause, "decidable": root_cause is not None, "variant": case["timeline"][0]["event"].split(": ", 1)[1]})
    manifest = {"seed": SEED, "cases": sum(len(rows) for rows in generated.values()), "splits": {}}
    output.mkdir(parents=True, exist_ok=True)
    for split, rows in generated.items():
        rows.sort(key=lambda item: item["incident_id"])
        labels[split].sort(key=lambda item: item["incident_id"])
        data_path = output / f"{split}.jsonl"
        labels_dir = output / "labels"
        labels_dir.mkdir(exist_ok=True)
        label_path = labels_dir / f"{split}.json"
        data_bytes = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()
        label_bytes = (json.dumps({"labels": labels[split]}, ensure_ascii=False, indent=2) + "\n").encode()
        data_path.write_bytes(data_bytes)
        label_path.write_bytes(label_bytes)
        manifest["splits"][split] = {"cases": len(rows), "data_sha256": hashlib.sha256(data_bytes).hexdigest(), "labels_sha256": hashlib.sha256(label_bytes).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2))
