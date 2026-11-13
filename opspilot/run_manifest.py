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
PUBLIC_MANIFEST_VERSION = 2
PUBLIC_EVENT_TYPES = ("timeout", "retry", "budget_exceeded", "cancel", "unsafe_action_blocked", "checkpoint_resume")
PUBLIC_MANIFEST_REQUIRED = {
    "schema_version", "project", "run_id", "trace_id", "evaluation_id", "environment", "git_sha",
    "dataset_id", "dataset_sha256", "truth_sha256", "report_sha256", "prompt_sha256", "tool_schema_sha256", "scorer_sha256",
    "split", "runtime", "model_id", "model_version", "gold_source", "budget", "cost_usd", "max_steps",
    "max_tokens", "status", "conformance_status", "started_at", "completed_at", "field_status",
    "blocked_fields", "trace_event_coverage", "source_artifacts", "source_status", "normalizer_sha256",
}
PUBLIC_FIELD_STATES = {"recorded", "not_applicable", "not_recorded", "blocked"}
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


def normalize_host_manifest(source: dict[str, Any], *, source_manifest_sha256: str,
                            report_sha256: str, truth_sha256: str,
                            normalizer_sha256: str) -> dict[str, Any]:
    """Create public shared-schema v2 view without filling absent host-run facts."""
    if source.get("dataset_sha256") != truth_sha256 or source.get("report_sha256") != report_sha256:
        raise ValueError("host manifest artifact hashes do not match source files")
    events = source.get("events")
    if not isinstance(events, list):
        events = []
    trace_event_coverage = {
        event_type: {"count": sum(isinstance(event, dict) and event.get("event_type") == event_type for event in events),
                     "coverage": "covered" if any(isinstance(event, dict) and event.get("event_type") == event_type for event in events) else "not_covered"}
        for event_type in PUBLIC_EVENT_TYPES
    }
    field_status = {
        "evaluation_id": "not_recorded", "environment": "not_recorded", "dataset_sha256": "recorded",
        "truth_sha256": "recorded", "report_sha256": "recorded", "prompt_sha256": "not_applicable",
        "tool_schema_sha256": "not_applicable",
        "scorer_sha256": "blocked", "runtime.digest_sha256": "blocked",
        "runtime.platform": "blocked", "runtime.lock_sha256": "blocked",
        "runtime.python": "recorded", "runtime.container_image": "recorded",
        "runtime.docker_cli_version": "recorded", "runtime.docker_engine_version": "blocked",
        "model_version": "not_applicable", "max_tokens": "not_applicable",
        "budget.cap_usd": "blocked", "budget.spent_usd": "recorded",
    }
    field_status.update(evaluation_id="blocked", environment="blocked")
    blocked_fields = sorted(field for field, state in field_status.items() if state == "blocked")
    runtime = {
        "digest_sha256": None,
        "python": source.get("python_version"),
        "platform": None,
        "lock_sha256": None,
        "container_image": source.get("service_image"),
        "docker_cli_version": source.get("docker_cli_client"),
        "docker_engine_version": None,
    }
    value = {
        "schema_version": PUBLIC_MANIFEST_VERSION,
        "normalizer_sha256": normalizer_sha256,
        "project": source.get("project"), "run_id": source.get("run_id"),
        "trace_id": source.get("trace_id"), "evaluation_id": None, "environment": None,
        "git_sha": source.get("git_sha"), "dataset_id": source.get("dataset_id"),
        "dataset_sha256": truth_sha256, "truth_sha256": truth_sha256, "report_sha256": report_sha256,
        "prompt_sha256": None, "tool_schema_sha256": None, "scorer_sha256": None, "split": source.get("split"),
        "runtime": runtime, "model_id": source.get("model_id"), "model_version": None,
        "gold_source": source.get("gold_source"),
        "budget": {"cap_usd": None, "spent_usd": source.get("cost_usd_estimate"), "status": "cap_not_recorded"},
        "cost_usd": source.get("cost_usd_estimate"), "max_steps": source.get("max_steps"),
        "max_tokens": None,
        "status": ("passed" if source.get("status") == "completed"
                   and source.get("passed_count") == source.get("case_count")
                   and source.get("failed_count") == 0 else "blocked"),
        "source_status": source.get("status"), "conformance_status": "blocked" if blocked_fields else "passed",
        "started_at": source.get("started_at"), "completed_at": source.get("ended_at"),
        "field_status": field_status, "blocked_fields": blocked_fields,
        "trace_event_coverage": trace_event_coverage,
        "source_artifacts": {"manifest_sha256": source_manifest_sha256,
                             "manifest_path": "reports/op1-host-matrix-run-manifest.json",
                             "report_path": source.get("report_path"),
                             "truth_path": source.get("gold_source")},
    }
    errors = validate_public_manifest(value)
    if errors:
        raise ValueError("invalid normalized public manifest: " + "; ".join(errors))
    return value


def validate_public_manifest(value: Any) -> list[str]:
    """Validate public run manifest v2; nullable provenance needs an explicit state."""
    if not isinstance(value, dict):
        return ["public manifest must be an object"]
    errors: list[str] = []
    missing = PUBLIC_MANIFEST_REQUIRED - value.keys()
    if missing:
        errors.append("missing public manifest fields: " + ", ".join(sorted(missing)))
    if value.get("schema_version") != PUBLIC_MANIFEST_VERSION or isinstance(value.get("schema_version"), bool):
        errors.append("unsupported public manifest schema_version")
    def walk(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                if child_key != "max_tokens" and SENSITIVE_KEYS.search(str(child_key)):
                    errors.append(f"sensitive field is forbidden: {child_key}")
                walk(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                walk(child, key)
        elif isinstance(item, str) and item.strip().casefold() == "unknown":
            errors.append(f"unknown placeholder is forbidden: {key}")
    walk(value)
    for field in ("project", "run_id", "trace_id", "dataset_id", "split", "gold_source"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{field} must be a non-empty string")
    sha40 = re.compile(r"^[0-9a-f]{40}$")
    for field in ("git_sha",):
        if not isinstance(value.get(field), str) or not sha40.fullmatch(value[field]):
            errors.append(f"{field} must be 40 hexadecimal characters")
    sha256_fields = ("dataset_sha256", "truth_sha256", "report_sha256")
    for field in sha256_fields:
        if not isinstance(value.get(field), str) or not SHA256.fullmatch(value[field]):
            errors.append(f"{field} must be a recorded SHA-256")
    if not isinstance(value.get("normalizer_sha256"), str) or not SHA256.fullmatch(value["normalizer_sha256"]):
        errors.append("normalizer_sha256 must be a recorded SHA-256")
    field_status = value.get("field_status")
    if not isinstance(field_status, dict):
        errors.append("field_status must be an object")
        field_status = {}
    for field in ("evaluation_id", "environment", "prompt_sha256", "tool_schema_sha256", "scorer_sha256", "model_version"):
        field_value = value.get(field)
        state = field_status.get(field)
        if field_value is None and state not in PUBLIC_FIELD_STATES:
            errors.append(f"null {field} requires explicit field_status")
        if field_value is not None and (not isinstance(field_value, str) or not field_value.strip()):
            errors.append(f"{field} must be a non-empty string or null")
    for field in ("prompt_sha256", "tool_schema_sha256", "scorer_sha256"):
        field_value = value.get(field)
        state = field_status.get(field)
        if field_value is None and state not in PUBLIC_FIELD_STATES:
            errors.append(f"null {field} requires explicit field_status")
        elif field_value is not None and (not isinstance(field_value, str) or not SHA256.fullmatch(field_value)):
            errors.append(f"{field} must be a SHA-256 or explicitly null")
        elif field_value is not None and state != "recorded":
            errors.append(f"recorded {field} requires recorded field_status")
    if value.get("model_id") != "none" and (not isinstance(value.get("model_id"), str) or not value["model_id"].strip()):
        errors.append("model_id must identify model or declare none")
    if value.get("model_id") == "none" and value.get("cost_usd") != 0:
        errors.append("model_id none requires zero cost")
    if value.get("model_id") == "none":
        if value.get("model_version") is not None or value.get("prompt_sha256") is not None or value.get("tool_schema_sha256") is not None:
            errors.append("model_id none requires model version/prompt/tool hashes not_applicable")
    cost = value.get("cost_usd")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
        errors.append("cost_usd must be a non-negative number")
    if value.get("environment") is None and field_status.get("environment") not in PUBLIC_FIELD_STATES:
        errors.append("null environment requires explicit field_status")
    budget = value.get("budget")
    if not isinstance(budget, dict) or not {"cap_usd", "spent_usd", "status"} <= budget.keys():
        errors.append("budget fields missing")
    elif (budget["spent_usd"] != cost or (budget["cap_usd"] is not None
          and (not isinstance(budget["cap_usd"], (int, float)) or isinstance(budget["cap_usd"], bool) or budget["cap_usd"] < 0))):
        errors.append("budget values must match the recorded run cost")
    runtime = value.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("runtime must be an object")
    else:
        runtime_fields = ("digest_sha256", "python", "platform", "lock_sha256", "container_image",
                          "docker_cli_version", "docker_engine_version")
        for field in runtime_fields:
            if field not in runtime:
                errors.append(f"runtime missing field: {field}")
        for field in ("digest_sha256", "lock_sha256"):
            digest = runtime.get(field)
            state = field_status.get(f"runtime.{field}")
            if digest is None and state not in PUBLIC_FIELD_STATES:
                errors.append(f"null runtime.{field} requires explicit field_status")
            elif digest is not None and (not isinstance(digest, str) or not SHA256.fullmatch(digest)):
                errors.append(f"runtime.{field} must be a SHA-256 or explicitly null")
        for field in ("platform", "python", "container_image", "docker_cli_version", "docker_engine_version"):
            if runtime.get(field) is None and field_status.get(f"runtime.{field}") not in PUBLIC_FIELD_STATES:
                errors.append(f"null runtime.{field} requires explicit field_status")
    for field in ("started_at", "completed_at"):
        try:
            stamp = datetime.fromisoformat(str(value[field]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append(f"{field} must be an ISO-8601 timestamp with timezone")
    if not any(error.startswith("started_at") or error.startswith("completed_at") for error in errors):
        start = datetime.fromisoformat(value["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(value["completed_at"].replace("Z", "+00:00"))
        if end < start:
            errors.append("completed_at must not precede started_at")
    coverage = value.get("trace_event_coverage")
    expected_events = set(PUBLIC_EVENT_TYPES)
    if not isinstance(coverage, dict) or set(coverage) != expected_events:
        errors.append("trace_event_coverage must declare the standard vocabulary")
    else:
        for event_type, item in coverage.items():
            if (not isinstance(item, dict) or not isinstance(item.get("count"), int)
                    or isinstance(item.get("count"), bool) or item["count"] < 0
                    or item.get("coverage") != ("covered" if item["count"] > 0 else "not_covered")):
                errors.append(f"invalid trace event coverage: {event_type}")
    source_artifacts = value.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        errors.append("source_artifacts must be an object")
    elif not isinstance(source_artifacts.get("manifest_sha256"), str) or not SHA256.fullmatch(source_artifacts["manifest_sha256"]):
        errors.append("source manifest SHA-256 is required")
    if value.get("blocked_fields") != sorted(path for path, state in field_status.items() if state == "blocked"):
        errors.append("blocked_fields must match blocked field_status entries")
    if value.get("conformance_status") not in {"passed", "blocked"}:
        errors.append("invalid conformance_status")
    if value.get("status") not in {"passed", "failed", "blocked"}:
        errors.append("invalid public run status")
    for field in ("max_steps",):
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            errors.append(f"{field} must be a positive integer")
    if value.get("max_tokens") is not None and (not isinstance(value["max_tokens"], int)
            or isinstance(value["max_tokens"], bool) or value["max_tokens"] <= 0):
        errors.append("max_tokens must be null or a positive integer")
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
