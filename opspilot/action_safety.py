"""One-time, signed approvals for simulation-only local remediation previews."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import threading
from typing import Any
import uuid

from .identity import Principal
from .run_manifest import build_op_run_manifest, current_git_sha, validate_op_run_artifact


ROOT = Path(__file__).resolve().parents[1]


ALLOWED_ENVIRONMENT = "test"
ACTION_SCOPES = {
    "restart_service": {"operator"},
    "rollback_release": {"release_manager"},
}
SERVICE_TARGETS = {"gateway", "orders", "inventory", "payment"}
_LEDGER_LOCK = threading.Lock()


class ActionRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _time(value: datetime | None = None) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return instant.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _time(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _signed_payload(approval: dict[str, Any]) -> bytes:
    return json.dumps({key: value for key, value in approval.items() if key != "signature"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approval_binding(report: dict[str, Any], *, action: str, target: str,
                     environment: str) -> dict[str, str]:
    """Summarize one unique investigation, its evidence pointers, and proposed action."""
    case_id = report.get("incident_id")
    run_id = report.get("run_id")
    recommendation = report.get("recommendation")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ActionRejected("investigation_case_id_missing")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ActionRejected("investigation_run_id_missing")
    if not isinstance(recommendation, dict) or not recommendation:
        raise ActionRejected("investigation_recommendation_missing")

    references = []
    for ref in report.get("evidence_refs", []):
        if isinstance(ref, dict) and isinstance(ref.get("source"), str) and isinstance(ref.get("record_id"), str):
            references.append({"source": ref["source"], "record_id": ref["record_id"]})
    query_summary = []
    for query in report.get("query_trace", []):
        if not isinstance(query, dict):
            continue
        source = query.get("source")
        rows = query.get("records", [])
        if not isinstance(source, str) or not isinstance(rows, list):
            continue
        ids = sorted(row["record_id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("record_id"), str))
        references.extend({"source": source, "record_id": record_id} for record_id in ids)
        query_summary.append({"source": source, "status": query.get("status"),
            "params": query.get("params"), "record_ids": ids})
    references = sorted({(ref["source"], ref["record_id"]) for ref in references})
    if not references:
        raise ActionRejected("investigation_evidence_missing")
    evidence_sha256 = _digest({"case_id": case_id, "run_id": run_id,
        "references": [{"source": source, "record_id": record_id} for source, record_id in references],
        "queries": sorted(query_summary, key=lambda item: item["source"])})
    recommendation_sha256 = _digest({"recommendation": recommendation, "action": action,
        "target": target, "environment": environment})
    return {"case_id": case_id, "investigation_run_id": run_id,
        "evidence_sha256": evidence_sha256, "recommendation_sha256": recommendation_sha256}


def load_or_create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        try:
            with path.open("xb") as stream:
                stream.write(key)
        except FileExistsError:
            key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    return key


def issue_approval(*, case_id: str, action: str, target: str, environment: str,
                   principal: Principal, investigation_run_id: str,
                   evidence_sha256: str, recommendation_sha256: str,
                   key: bytes, ttl_seconds: int = 120,
                   now: datetime | None = None) -> dict[str, Any]:
    if not case_id or len(case_id) > 100:
        raise ActionRejected("invalid_case_id")
    if environment != ALLOWED_ENVIRONMENT:
        raise ActionRejected("environment_not_allowed")
    if action not in ACTION_SCOPES or target not in SERVICE_TARGETS:
        raise ActionRejected("action_or_target_not_allowed")
    if not isinstance(principal, Principal) or not principal.authenticated:
        raise ActionRejected("actor_identity_unverified")
    allowed_roles = ACTION_SCOPES[action].intersection(principal.roles)
    if not allowed_roles:
        raise ActionRejected("role_scope_denied")
    if not principal.subject or len(principal.subject) > 120:
        raise ActionRejected("approver_required")
    role = sorted(allowed_roles)[0]
    if not isinstance(investigation_run_id, str) or not investigation_run_id.strip():
        raise ActionRejected("investigation_run_id_missing")
    for digest in (evidence_sha256, recommendation_sha256):
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ActionRejected("investigation_digest_invalid")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 300:
        raise ActionRejected("approval_ttl_out_of_range")
    if len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    issued = _time(now)
    approval = {
        "approval_id": str(uuid.uuid4()), "nonce": secrets.token_urlsafe(24),
        "case_id": case_id, "action": action, "target": target,
        "environment": environment, "role": role, "approved_by": principal.subject,
        "investigation_run_id": investigation_run_id, "evidence_sha256": evidence_sha256,
        "recommendation_sha256": recommendation_sha256,
        "target_environment": environment,
        "issued_at": _iso(issued), "expires_at": _iso(issued + timedelta(seconds=ttl_seconds)),
    }
    approval["signature"] = hmac.new(key, _signed_payload(approval), hashlib.sha256).hexdigest()
    return approval


def _audit(ledger_path: Path, event: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_LOCK:
        _append_unlocked(ledger_path, event)


def _append_unlocked(ledger_path: Path, event: dict[str, Any]) -> None:
    with ledger_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _reject(ledger_path: Path, approval: dict[str, Any], code: str, now: datetime) -> None:
    _audit(ledger_path, {"event_id": str(uuid.uuid4()), "event_type": "dry_run_rejected",
        "timestamp": _iso(now), "approval_id": approval.get("approval_id"),
        "case_id": approval.get("case_id"), "reason": code, "external_system_calls": 0})
    raise ActionRejected(code)


def execute_dry_run(approval: dict[str, Any], *, key: bytes, environment: str,
                    ledger_path: Path, investigation_report: dict[str, Any],
                    principal: Principal,
                    now: datetime | None = None) -> dict[str, Any]:
    instant = _time(now)
    if len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    if environment != ALLOWED_ENVIRONMENT or approval.get("environment") != environment:
        _reject(ledger_path, approval, "environment_mismatch", instant)
    if approval.get("action") not in ACTION_SCOPES or approval.get("target") not in SERVICE_TARGETS:
        _reject(ledger_path, approval, "action_or_target_not_allowed", instant)
    if approval.get("role") not in ACTION_SCOPES[approval["action"]]:
        _reject(ledger_path, approval, "role_scope_denied", instant)
    if (not isinstance(principal, Principal) or not principal.authenticated
            or principal.subject != approval.get("approved_by")
            or approval.get("role") not in principal.roles):
        _reject(ledger_path, approval, "actor_principal_mismatch", instant)
    signature = approval.get("signature")
    expected = hmac.new(key, _signed_payload(approval), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        _reject(ledger_path, approval, "approval_signature_invalid", instant)
    try:
        expires = _time(datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00")))
        issued = _time(datetime.fromisoformat(str(approval["issued_at"]).replace("Z", "+00:00")))
    except (KeyError, ValueError, TypeError):
        _reject(ledger_path, approval, "approval_time_invalid", instant)
    if expires <= instant or issued > instant or (expires - issued).total_seconds() > 300:
        _reject(ledger_path, approval, "approval_expired_or_invalid", instant)
    if not approval.get("nonce") or not approval.get("approval_id"):
        _reject(ledger_path, approval, "approval_identity_missing", instant)
    required_binding = ("investigation_run_id", "evidence_sha256", "recommendation_sha256", "target_environment")
    if any(not approval.get(field) for field in required_binding):
        _reject(ledger_path, approval, "approval_binding_missing", instant)
    try:
        current_binding = approval_binding(investigation_report, action=approval["action"],
            target=approval["target"], environment=environment)
    except ActionRejected as exc:
        _reject(ledger_path, approval, exc.code, instant)
    if (approval.get("case_id") != current_binding["case_id"]
            or approval.get("investigation_run_id") != current_binding["investigation_run_id"]
            or approval.get("evidence_sha256") != current_binding["evidence_sha256"]
            or approval.get("recommendation_sha256") != current_binding["recommendation_sha256"]
            or approval.get("target_environment") != environment):
        _reject(ledger_path, approval, "approval_investigation_mismatch", instant)
    if approval["action"] == "rollback_release":
        simulated = {"before_version": "v2", "after_version": "v1", "rollback": "simulated"}
        checks = ["target_service_matches", "previous_version_selected", "health_check_simulated"]
    else:
        simulated = {"before_state": "running", "after_state": "running", "restart": "simulated"}
        checks = ["target_service_matches", "service_state_simulated"]
    result = {
        "event_id": str(uuid.uuid4()), "event_type": "dry_run_completed",
        "status": "simulated_execution", "case_id": approval["case_id"],
        "action": approval["action"], "target": approval["target"], "environment": environment,
        "approval_id": approval["approval_id"], "nonce": approval["nonce"],
        "approved_by": approval["approved_by"], "timestamp": _iso(instant),
        "external_system_calls": 0, "external_writes": 0,
        "simulation": simulated,
        "rollback_verification": {"status": "simulated_pass", "checks": checks,
            "real_environment_verified": False},
    }
    source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["op_run_manifest"] = build_op_run_manifest(
        project="OpsPilot", run_id=result["event_id"], evaluation_id="op4_remediation_dry_run",
        dataset_id="op4_investigation_report", dataset_sha256=_digest(investigation_report),
        truth_sha256=None, split="test_dry_run", evaluator_sha256=source_sha,
        result_fingerprint_sha256=_digest(result), git_sha=current_git_sha(ROOT), model_id="none",
        prompt_sha=None, tool_schema_sha=None, runtime_digest=None, max_steps=1, max_tokens=None,
        scorer_sha=source_sha, gold_source="not_applicable", cost_usd_estimate=0.0,
        trace_id=result["event_id"], started_at=_iso(instant), ended_at=_iso(instant),
        parameters={"artifact_type": "action_dry_run", "case_id": approval["case_id"],
            "action": approval["action"], "target": approval["target"], "environment": environment,
            "approval_id": approval["approval_id"], "external_system_calls": 0, "external_writes": 0,
            "real_environment_verified": False}, status="completed")
    manifest_errors = validate_op_run_artifact(result, is_new_run=True)
    if manifest_errors:
        raise ValueError("refusing to record unmanifested OP-4 dry run: " + "; ".join(manifest_errors))
    with _LEDGER_LOCK:
        if ledger_path.exists():
            try:
                events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except (OSError, json.JSONDecodeError):
                raise ActionRejected("action_ledger_unreadable")
            if any(not isinstance(event, dict) or not isinstance(event.get("event_type"), str)
                    for event in events):
                raise ActionRejected("action_ledger_unreadable")
            if any(event.get("event_type") == "dry_run_completed" and event.get("nonce") == approval["nonce"] for event in events):
                _append_unlocked(ledger_path, {"event_id": str(uuid.uuid4()), "event_type": "dry_run_rejected",
                    "timestamp": _iso(instant), "approval_id": approval.get("approval_id"),
                    "case_id": approval.get("case_id"), "reason": "approval_replayed", "external_system_calls": 0})
                raise ActionRejected("approval_replayed")
        _append_unlocked(ledger_path, result)
    return result
