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
OP_RUN_MANIFEST_VERSION = 1
OP_RUN_MANIFEST_REQUIRED = {
    "schema_version", "project", "run_id", "evaluation_id", "dataset_id", "dataset_sha256",
    "truth_sha256", "split", "evaluator_sha256", "result_fingerprint_sha256", "status",
    "git_sha", "model_id", "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps",
    "max_tokens", "scorer_sha", "gold_source", "cost_usd_estimate", "trace_id", "started_at", "ended_at",
    "parameters",
}
OP_RUN_IDENTITY_FIELDS = ("project", "run_id", "evaluation_id", "dataset_id", "split", "model_id", "gold_source", "trace_id")
OP_RUN_HASH_FIELDS = ("dataset_sha256", "truth_sha256", "evaluator_sha256", "result_fingerprint_sha256", "prompt_sha", "tool_schema_sha", "runtime_digest", "scorer_sha")


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
                elif event["run_id"] != value.get("run_id") or event["trace_id"] != value.get("trace_id"):
                    errors.append("event run_id and trace_id must match manifest identity")
    if "telemetry_evidence" in value:
        evidence = value["telemetry_evidence"]
        if not isinstance(evidence, list):
            errors.append("telemetry_evidence must be a list")
        else:
            for item in evidence:
                required_evidence = ("run_id", "trace_id", "span_id", "service", "operation", "order_id")
                if not isinstance(item, dict) or any(not item.get(key) for key in required_evidence):
                    errors.append("telemetry evidence requires run_id, trace_id, span_id, service, operation, and order_id")
                elif item.get("event_type") != "span_observed":
                    errors.append("telemetry evidence event_type must be span_observed")
                if isinstance(item, dict) and item.get("run_id") and item["run_id"] != value.get("run_id"):
                    errors.append("telemetry evidence run_id must match manifest identity")
                if isinstance(item, dict) and (not isinstance(item.get("start_unix_us"), int) or not isinstance(item.get("duration_us"), int) or item["duration_us"] < 0):
                    errors.append("telemetry evidence requires integer start_unix_us and non-negative duration_us")
    for field in ("max_steps", "max_tokens"):
        optional_model_tokens = field in value and field == "max_tokens" and value.get("model_id") == "none" and value[field] is None
        if field in value and not optional_model_tokens and (not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] <= 0):
            errors.append(f"{field} must be a positive integer")
    return errors


def validate_op_run_manifest(value: Any) -> list[str]:
    """Validate the minimal identity and reproducibility contract for new OP runs."""
    if not isinstance(value, dict):
        return ["OP run manifest must be an object"]
    errors: list[str] = []
    missing = OP_RUN_MANIFEST_REQUIRED - value.keys()
    if missing:
        errors.append("missing required OP run fields: " + ", ".join(sorted(missing)))
    version = value.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != OP_RUN_MANIFEST_VERSION:
        errors.append("unsupported OP run schema_version")
    for field in OP_RUN_IDENTITY_FIELDS:
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            errors.append(f"{field} must be a non-empty string")
    for field in OP_RUN_HASH_FIELDS:
        optional_mock_hash = value.get("model_id") == "none" and field in {"prompt_sha", "tool_schema_sha", "runtime_digest"} and value.get(field) is None
        if field in value and not optional_mock_hash and (not isinstance(value[field], str) or not SHA256.fullmatch(value[field])):
            errors.append(f"{field} must be 64 hexadecimal characters")
    if "git_sha" in value and (not isinstance(value["git_sha"], str) or not SHA.fullmatch(value["git_sha"])):
        errors.append("git_sha must be 40 hexadecimal characters")
    for field in ("max_steps", "max_tokens"):
        optional_mock_tokens = field == "max_tokens" and value.get("model_id") == "none" and value.get(field) is None
        if field in value and not optional_mock_tokens and (not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] <= 0):
            errors.append(f"{field} must be a positive integer")
    if "cost_usd_estimate" in value:
        cost = value["cost_usd_estimate"]
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
            errors.append("cost_usd_estimate must be a non-negative number")
        elif value.get("model_id") == "none" and cost != 0:
            errors.append("model_id none requires zero estimated cost")
    if "parameters" in value and (not isinstance(value["parameters"], dict) or not value["parameters"]):
        errors.append("parameters must be a non-empty object")
    if value.get("model_id") == "none":
        if value.get("gold_source") != "independent":
            errors.append("model_id none requires gold_source independent")
        for field in ("prompt_sha", "tool_schema_sha", "runtime_digest"):
            if field in value and value[field] is not None:
                errors.append(f"model_id none requires {field} null")
    elif isinstance(value.get("model_id"), str) and value.get("model_id").lower() in {"unknown", "n/a", ""}:
        errors.append("model_id must identify the model or be none")
    for field in ("started_at", "ended_at"):
        if field in value:
            try:
                stamp = datetime.fromisoformat(str(value[field]).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{field} must be an ISO-8601 timestamp with timezone")
    if "started_at" in value and "ended_at" in value and not any(e.startswith("started_at") or e.startswith("ended_at") for e in errors):
        if datetime.fromisoformat(value["ended_at"].replace("Z", "+00:00")) < datetime.fromisoformat(value["started_at"].replace("Z", "+00:00")):
            errors.append("ended_at must not precede started_at")
    if "status" in value and (not isinstance(value["status"], str) or value["status"] not in STATUSES):
        errors.append("status is not supported")
    return errors


def build_op_run_manifest(*, project: str, run_id: str, evaluation_id: str, dataset_id: str,
                          dataset_sha256: str, truth_sha256: str, split: str,
                          evaluator_sha256: str, result_fingerprint_sha256: str,
                          git_sha: str, model_id: str, prompt_sha: str | None, tool_schema_sha: str | None,
                          runtime_digest: str | None, max_steps: int, max_tokens: int | None,
                          scorer_sha: str, gold_source: str, cost_usd_estimate: float,
                          trace_id: str, started_at: str, ended_at: str, parameters: dict[str, Any],
                          status: str = "completed") -> dict[str, Any]:
    """Build and validate a new OP manifest before its report is written."""
    value = {
        "schema_version": OP_RUN_MANIFEST_VERSION, "project": project, "run_id": run_id,
        "evaluation_id": evaluation_id, "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256, "truth_sha256": truth_sha256, "split": split,
        "evaluator_sha256": evaluator_sha256, "result_fingerprint_sha256": result_fingerprint_sha256,
        "git_sha": git_sha, "model_id": model_id, "prompt_sha": prompt_sha,
        "tool_schema_sha": tool_schema_sha, "runtime_digest": runtime_digest,
        "max_steps": max_steps, "max_tokens": max_tokens, "scorer_sha": scorer_sha,
        "gold_source": gold_source, "cost_usd_estimate": cost_usd_estimate,
        "trace_id": trace_id, "started_at": started_at, "ended_at": ended_at,
        "parameters": parameters, "status": status,
    }
    errors = validate_op_run_manifest(value)
    if errors:
        raise ValueError("invalid OP run manifest: " + "; ".join(errors))
    return value


def validate_op_run_artifact(value: Any, *, is_new_run: bool) -> list[str]:
    """Keep historical reports readable; require a valid manifest for new runs."""
    if not isinstance(value, dict):
        return ["OP run artifact must be an object"]
    manifest = value.get("op_run_manifest")
    if manifest is None:
        return ["new OP run requires op_run_manifest"] if is_new_run else []
    return validate_op_run_manifest(manifest)
