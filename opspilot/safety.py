"""Local audit and approval-gated action simulator."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_AUDIT_LOCK = threading.Lock()


@contextmanager
def _audit_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with _AUDIT_LOCK, lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _event_id(event: dict[str, Any]) -> str:
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, hashlib.sha256(canonical.encode("utf-8")).hexdigest()))


def _read_events(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    events = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("audit event must be an object")
            event_id = event.get("event_id")
            if isinstance(event_id, str):
                events[event_id] = event
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("audit ledger is unreadable; refusing to append") from exc
    return events


def _append_events_once(path: Path, events: list[dict[str, Any]]) -> None:
    with _audit_file_lock(path):
        prior = _read_events(path)
        append = []
        for event in events:
            event_id = event["event_id"]
            existing = prior.get(event_id)
            if existing is not None:
                if existing != event:
                    raise ValueError("audit event_id conflicts with existing payload")
                continue
            append.append(event)
            prior[event_id] = event
        if append:
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                for event in append:
                    stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def audit_investigation(report: dict[str, Any], path: Path) -> None:
    events = []
    for query in report["query_trace"]:
        event = {
            "event_type": "readonly_query",
            "incident_id": report["incident_id"],
            "source": query["source"],
            "params": query["params"],
            "status": query["status"],
            "record_ids": [row["record_id"] for row in query["records"]],
            "fresh": query.get("fresh", True),
        }
        event["event_id"] = _event_id(event)
        events.append(event)
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    report_event = {
        "event_type": "investigation_report",
        "incident_id": report["incident_id"],
        "status": report["status"],
        "report_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    report_event["event_id"] = _event_id(report_event)
    events.append(report_event)
    _append_events_once(path, events)


def simulate_action(report: dict[str, Any], event_id: str, approval: str, audit_path: Path) -> dict[str, Any]:
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must not be empty")
    if approval not in {"approve", "reject"}:
        raise ValueError("approval must be approve or reject")
    recommendation = report.get("recommendation")
    if not recommendation:
        raise ValueError("incident has no determined action recommendation")
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
    with _audit_file_lock(audit_path):
        prior = _read_events(audit_path).get(event_id)
        if prior is not None:
            same_decision = (prior.get("event_type") == "approval_decision"
                and prior.get("incident_id") == report["incident_id"]
                and prior.get("action_id") == recommendation["action_id"]
                and prior.get("decision") == approval)
            return {"status": "duplicate_ignored" if same_decision else "event_id_conflict",
                "executed": False, "external_write": False, "event_id": event_id}
        with audit_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return result
