"""Versioned validation for OpsPilot run manifests."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

SCHEMA_VERSION = 1
REQUIRED = {
    "schema_version", "project", "git_sha", "dataset_id", "dataset_sha256",
    "split", "model_id", "prompt_sha", "tool_schema_sha", "runtime_digest",
    "max_steps", "max_tokens", "scorer_sha", "gold_source", "cost_usd_estimate",
    "started_at", "ended_at", "status",
    "run_id", "trace_id",
}
SENSITIVE_KEYS = re.compile(r"(api.?key|secret|password|token|private.?key|chain.?of.?thought|raw.?prompt)", re.I)
SHA = re.compile(r"^[0-9a-f]{40}$", re.I)
SHA256 = re.compile(r"^[0-9a-f]{64}$", re.I)
STATUSES = {"completed", "failed", "cancelled", "budget_exceeded"}
EVENT_TYPES = {"timeout", "retry", "budget_exceeded", "unsafe_action_blocked", "cancel", "checkpoint_resume"}


def validate_run_manifest(value: Any) -> list[str]:
    """Return validation errors; never mutates or serializes the supplied manifest."""
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["manifest must be an object"]
    missing = REQUIRED - value.keys()
    if missing:
        errors.append("missing required fields: " + ", ".join(sorted(missing)))
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    for key, item in value.items():
        if key != "max_tokens" and SENSITIVE_KEYS.search(str(key)):
            errors.append(f"sensitive field is forbidden: {key}")
    if "git_sha" in value and not SHA.fullmatch(str(value["git_sha"])):
        errors.append("git_sha must be 40 hexadecimal characters")
    if "dataset_sha256" in value and not SHA256.fullmatch(str(value["dataset_sha256"])):
        errors.append("dataset_sha256 must be 64 hexadecimal characters")
    for field in ("started_at", "ended_at"):
        if field in value:
            try:
                stamp = datetime.fromisoformat(str(value[field]).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{field} must be an ISO-8601 timestamp with timezone")
    if "started_at" in value and "ended_at" in value and not any(e.startswith("started_at") or e.startswith("ended_at") for e in errors):
        start = datetime.fromisoformat(str(value["started_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(value["ended_at"]).replace("Z", "+00:00"))
        if end < start:
            errors.append("ended_at must not precede started_at")
    if "status" in value and value["status"] not in STATUSES:
        errors.append("status is not supported")
    for field in ("run_id", "trace_id"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            errors.append(f"{field} must be a non-empty string")
    if "events" in value:
        if not isinstance(value["events"], list):
            errors.append("events must be a list")
        else:
            for event in value["events"]:
                if not isinstance(event, dict) or event.get("event_type") not in EVENT_TYPES:
                    errors.append("event has unsupported event_type")
                if not isinstance(event, dict) or not event.get("run_id") or not event.get("trace_id") or not event.get("span_id"):
                    errors.append("event requires run_id, trace_id, and span_id")
    for field in ("max_steps", "max_tokens"):
        if field in value and (not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] <= 0):
            errors.append(f"{field} must be a positive integer")
    return errors
