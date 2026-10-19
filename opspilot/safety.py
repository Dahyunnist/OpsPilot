"""Local audit and approval-gated action simulator."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def audit_investigation(report: dict[str, Any], path: Path) -> None:
    for query in report["query_trace"]:
        _append(path, {
            "event_id": str(uuid.uuid4()),
            "event_type": "readonly_query",
            "incident_id": report["incident_id"],
            "source": query["source"],
            "params": query["params"],
            "status": query["status"],
            "record_ids": [row["record_id"] for row in query["records"]],
            "fresh": query.get("fresh", True),
        })
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _append(path, {
        "event_id": str(uuid.uuid4()),
        "event_type": "investigation_report",
        "incident_id": report["incident_id"],
        "status": report["status"],
        "report_sha256": hashlib.sha256(canonical).hexdigest(),
    })


def simulate_action(report: dict[str, Any], event_id: str, approval: str, audit_path: Path) -> dict[str, Any]:
    if not event_id.strip():
        raise ValueError("event_id must not be empty")
    if approval not in {"approve", "reject"}:
        raise ValueError("approval must be approve or reject")
    recommendation = report.get("recommendation")
    if not recommendation:
        raise ValueError("incident has no determined action recommendation")
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if line:
                prior = json.loads(line)
                if prior.get("event_id") == event_id:
                    return {"status": "duplicate_ignored", "executed": False, "event_id": event_id}
    approved = approval == "approve"
    result = {
        "event_id": event_id,
        "event_type": "approval_decision",
        "incident_id": report["incident_id"],
        "action_id": recommendation["action_id"],
        "decision": approval,
        "status": "simulated_execution" if approved else "rejected_no_execution",
        "executed": approved,
        "external_write": False,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _append(audit_path, result)
    return result
