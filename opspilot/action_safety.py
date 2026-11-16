"""One-time, signed approvals for simulation-only local remediation previews."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
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
EVIDENCE_SOURCES = {"metrics", "logs", "traces", "deployments"}
_LEDGER_LOCK = threading.Lock()


class ActionRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _valid_principal(principal: Any) -> bool:
    return (isinstance(principal, Principal) and principal.authenticated is True
        and isinstance(principal.subject, str) and bool(principal.subject.strip())
        and isinstance(principal.issuer, str) and bool(principal.issuer.strip())
        and isinstance(principal.roles, (set, frozenset))
        and all(isinstance(role, str) and role for role in principal.roles))


@contextmanager
def _ledger_file_lock(ledger_path: Path):
    """Serialize ledger replay checks and appends across processes on one local filesystem."""
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
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
    if not isinstance(report, dict):
        raise ActionRejected("investigation_report_invalid")
    case_id = report.get("incident_id")
    run_id = report.get("run_id")
    recommendation = report.get("recommendation")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ActionRejected("investigation_case_id_missing")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ActionRejected("investigation_run_id_missing")
    if not isinstance(recommendation, dict) or not recommendation:
        raise ActionRejected("investigation_recommendation_missing")
    if recommendation.get("requires_approval") is not True:
        raise ActionRejected("investigation_recommendation_not_approval_gated")

    references = []
    report_references = set()
    evidence_refs = report.get("evidence_refs", [])
    query_trace = report.get("query_trace", [])
    if not isinstance(evidence_refs, list) or not isinstance(query_trace, list):
        raise ActionRejected("investigation_evidence_invalid")
    for ref in evidence_refs:
        if (not isinstance(ref, dict) or not isinstance(ref.get("source"), str)
                or ref["source"] not in EVIDENCE_SOURCES
                or not isinstance(ref.get("record_id"), str) or not ref["record_id"].strip()):
            raise ActionRejected("investigation_evidence_invalid")
        report_references.add((ref["source"], ref["record_id"]))
        references.append({"source": ref["source"], "record_id": ref["record_id"]})
    query_summary = []
    query_references = set()
    for query in query_trace:
        if not isinstance(query, dict):
            raise ActionRejected("investigation_evidence_invalid")
        source = query.get("source")
        rows = query.get("records", [])
        if not isinstance(source, str) or source not in EVIDENCE_SOURCES or not isinstance(rows, list):
            raise ActionRejected("investigation_evidence_invalid")
        if any(not isinstance(row, dict) or not isinstance(row.get("record_id"), str)
                or not row["record_id"].strip() for row in rows):
            raise ActionRejected("investigation_evidence_invalid")
        ids = sorted(row["record_id"] for row in rows)
        query_references.update((source, record_id) for record_id in ids)
        references.extend({"source": source, "record_id": record_id} for record_id in ids)
        query_summary.append({"source": source, "status": query.get("status"),
            "params": query.get("params"), "record_ids": ids})
    if not report_references:
        raise ActionRejected("investigation_evidence_missing")
    if not report_references <= query_references:
        raise ActionRejected("investigation_evidence_unresolved")
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
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    return key


def issue_approval(*, case_id: str, action: str, target: str, environment: str,
                   principal: Principal, investigation_run_id: str,
                   evidence_sha256: str, recommendation_sha256: str,
                   key: bytes, ttl_seconds: int = 120,
                   now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(case_id, str) or not case_id.strip() or len(case_id) > 100:
        raise ActionRejected("invalid_case_id")
    if environment != ALLOWED_ENVIRONMENT:
        raise ActionRejected("environment_not_allowed")
    if (not isinstance(action, str) or action not in ACTION_SCOPES
            or not isinstance(target, str) or target not in SERVICE_TARGETS):
        raise ActionRejected("action_or_target_not_allowed")
    if not _valid_principal(principal):
        raise ActionRejected("actor_identity_unverified")
    allowed_roles = ACTION_SCOPES[action].intersection(principal.roles)
    if not allowed_roles:
        raise ActionRejected("role_scope_denied")
    if len(principal.subject) > 120:
        raise ActionRejected("approver_required")
    if len(principal.issuer) > 120:
        raise ActionRejected("approver_issuer_invalid")
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
        "approved_by_issuer": principal.issuer,
        "investigation_run_id": investigation_run_id, "evidence_sha256": evidence_sha256,
        "recommendation_sha256": recommendation_sha256,
        "target_environment": environment,
        "issued_at": _iso(issued), "expires_at": _iso(issued + timedelta(seconds=ttl_seconds)),
    }
    approval["signature"] = hmac.new(key, _signed_payload(approval), hashlib.sha256).hexdigest()
    return approval


def _audit(ledger_path: Path, event: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_LOCK, _ledger_file_lock(ledger_path):
        _append_unlocked(ledger_path, event)


def _append_unlocked(ledger_path: Path, event: dict[str, Any]) -> None:
    with ledger_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _event_identity() -> dict[str, str]:
    """Return local run and span correlation IDs for one redacted audit event."""
    return {
        "event_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "trace_id": uuid.uuid4().hex,
        "span_id": uuid.uuid4().hex[:16],
    }


def _nonce_sha256(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _event_matches_nonce(event: dict[str, Any], nonce: str) -> bool:
    # Read legacy local ledgers that stored raw nonces; all new events store hashes.
    return event.get("nonce_sha256") == _nonce_sha256(nonce) or event.get("nonce") == nonce


def record_approval_issued(approval: dict[str, Any], *, ledger_path: Path,
                           now: datetime | None = None) -> dict[str, Any]:
    """Persist a redacted local event after the signed test-only ticket is stored."""
    instant = _time(now)
    event = {**_event_identity(), "event_type": "approval_issued", "timestamp": _iso(instant),
        "approval_id": approval["approval_id"], "case_id": approval["case_id"],
        "action": approval["action"], "target": approval["target"],
        "environment": approval["environment"],
        "investigation_run_id": approval["investigation_run_id"],
        "expires_at": approval["expires_at"], "external_system_calls": 0, "external_writes": 0}
    _audit(ledger_path, event)
    return event


def record_approval_issue_rejected(*, ledger_path: Path, case_id: str, action: str,
                                   target: str, environment: str,
                                   reason: str = "human_confirmation_mismatch",
                                   now: datetime | None = None) -> dict[str, Any]:
    """Record a fixed approval rejection code; never persist identity or confirmation input."""
    allowed_reasons = {"human_confirmation_mismatch", "actor_resolver_unconfigured",
        "actor_identity_required", "actor_identity_unverified", "role_scope_denied",
        "investigation_report_unreadable", "investigation_report_invalid",
        "investigation_case_id_missing", "investigation_run_id_missing",
        "investigation_recommendation_missing", "investigation_recommendation_not_approval_gated",
        "investigation_evidence_invalid", "investigation_evidence_missing",
        "investigation_evidence_unresolved", "investigation_case_mismatch"}
    if reason not in allowed_reasons:
        raise ValueError("unsupported_approval_issue_rejection")
    instant = _time(now)
    event = {**_event_identity(), "event_type": "approval_issue_rejected", "timestamp": _iso(instant),
        "case_id": case_id, "action": action, "target": target, "environment": environment,
        "reason": reason,
        "external_system_calls": 0, "external_writes": 0}
    _audit(ledger_path, event)
    return event


def record_approval_revoke_rejected(approval: dict[str, Any] | None, *, ledger_path: Path,
                                    reason: str = "human_revocation_confirmation_mismatch",
                                    now: datetime | None = None) -> dict[str, Any]:
    """Record a failed revoke confirmation without retaining its supplied text."""
    if reason not in {"human_revocation_confirmation_mismatch", "approval_file_unreadable"}:
        raise ValueError("unsupported_approval_revoke_rejection")
    instant = _time(now)
    event = {**_event_identity(), "event_type": "approval_revoke_rejected",
        "timestamp": _iso(instant),
        "approval_id": approval.get("approval_id") if isinstance(approval, dict) else None,
        "case_id": approval.get("case_id") if isinstance(approval, dict) else None,
        "reason": reason,
        "external_system_calls": 0, "external_writes": 0}
    _audit(ledger_path, event)
    return event


def record_cli_arguments_rejected(command: str, *, ledger_path: Path,
                                  now: datetime | None = None) -> dict[str, Any]:
    """Record malformed arguments without retaining raw arguments or parser text."""
    if command not in {"approve", "dry-run", "revoke"}:
        raise ValueError("unsupported_cli_rejection_command")
    instant = _time(now)
    event = {**_event_identity(), "event_type": "cli_arguments_rejected",
        "timestamp": _iso(instant), "command": command,
        "reason": "cli_arguments_invalid", "external_system_calls": 0, "external_writes": 0}
    _audit(ledger_path, event)
    return event


def _read_ledger_unlocked(ledger_path: Path) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    try:
        events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActionRejected("action_ledger_unreadable") from exc
    if any(not isinstance(event, dict) or not isinstance(event.get("event_type"), str) for event in events):
        raise ActionRejected("action_ledger_unreadable")
    return events


def _reject(ledger_path: Path, approval: dict[str, Any], code: str, now: datetime) -> None:
    _audit(ledger_path, {**_event_identity(), "event_type": "dry_run_rejected",
        "timestamp": _iso(now), "approval_id": approval.get("approval_id"),
        "case_id": approval.get("case_id"), "reason": code,
        "external_system_calls": 0, "external_writes": 0})
    raise ActionRejected(code)


def _reject_revoke(ledger_path: Path, approval: dict[str, Any], code: str, now: datetime) -> None:
    _audit(ledger_path, {**_event_identity(), "event_type": "approval_revoke_rejected",
        "timestamp": _iso(now), "approval_id": approval.get("approval_id"),
        "case_id": approval.get("case_id"), "reason": code,
        "external_system_calls": 0, "external_writes": 0})
    raise ActionRejected(code)


def revoke_approval(approval: dict[str, Any], *, key: bytes, ledger_path: Path,
                    principal: Principal, now: datetime | None = None) -> dict[str, Any]:
    """Revoke a signed approval before use; serialize revocation against dry-run consumption."""
    instant = _time(now)
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    if not isinstance(approval, dict):
        _reject_revoke(ledger_path, {}, "approval_payload_invalid", instant)
    action, target = approval.get("action"), approval.get("target")
    role = approval.get("role")
    if (approval.get("environment") != ALLOWED_ENVIRONMENT or approval.get("target_environment") != ALLOWED_ENVIRONMENT
            or not isinstance(action, str) or action not in ACTION_SCOPES
            or not isinstance(target, str) or target not in SERVICE_TARGETS):
        _reject_revoke(ledger_path, approval, "action_or_target_not_allowed", instant)
    if not isinstance(role, str) or role not in ACTION_SCOPES[action]:
        _reject_revoke(ledger_path, approval, "role_scope_denied", instant)
    if (not _valid_principal(principal)
            or principal.subject != approval.get("approved_by")
            or principal.issuer != approval.get("approved_by_issuer") or role not in principal.roles):
        _reject_revoke(ledger_path, approval, "actor_principal_mismatch", instant)
    signature = approval.get("signature")
    try:
        expected = hmac.new(key, _signed_payload(approval), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        _reject_revoke(ledger_path, approval, "approval_signature_invalid", instant)
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        _reject_revoke(ledger_path, approval, "approval_signature_invalid", instant)
    approval_id, nonce = approval.get("approval_id"), approval.get("nonce")
    if not isinstance(approval_id, str) or not approval_id or not isinstance(nonce, str) or not nonce:
        _reject_revoke(ledger_path, approval, "approval_identity_missing", instant)

    with _LEDGER_LOCK, _ledger_file_lock(ledger_path):
        events = _read_ledger_unlocked(ledger_path)
        if any(event.get("event_type") == "approval_revoked" and _event_matches_nonce(event, nonce) for event in events):
            return {"status": "approval_already_revoked", "approval_id": approval_id,
                "external_system_calls": 0, "external_writes": 0}
        if any(event.get("event_type") == "dry_run_completed" and _event_matches_nonce(event, nonce) for event in events):
            _append_unlocked(ledger_path, {**_event_identity(), "event_type": "approval_revoke_rejected",
                "timestamp": _iso(instant), "approval_id": approval_id, "case_id": approval.get("case_id"),
                "reason": "approval_already_consumed", "external_system_calls": 0, "external_writes": 0})
            raise ActionRejected("approval_already_consumed")
        event = {**_event_identity(), "event_type": "approval_revoked",
            "timestamp": _iso(instant), "approval_id": approval_id, "nonce_sha256": _nonce_sha256(nonce),
            "case_id": approval.get("case_id"),
            "revoker_ref": hashlib.sha256((principal.issuer + "\0" + principal.subject).encode("utf-8")).hexdigest(),
            "external_system_calls": 0, "external_writes": 0}
        _append_unlocked(ledger_path, event)
    return {"status": "approval_revoked", "approval_id": approval_id,
        "external_system_calls": 0, "external_writes": 0}


def execute_dry_run(approval: dict[str, Any], *, key: bytes, environment: str,
                    ledger_path: Path, investigation_report: dict[str, Any],
                    principal: Principal,
                    now: datetime | None = None) -> dict[str, Any]:
    instant = _time(now)
    if not isinstance(approval, dict):
        _reject(ledger_path, {}, "approval_payload_invalid", instant)
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    if environment != ALLOWED_ENVIRONMENT or approval.get("environment") != environment:
        _reject(ledger_path, approval, "environment_mismatch", instant)
    action, target = approval.get("action"), approval.get("target")
    if (not isinstance(action, str) or action not in ACTION_SCOPES
            or not isinstance(target, str) or target not in SERVICE_TARGETS):
        _reject(ledger_path, approval, "action_or_target_not_allowed", instant)
    if not isinstance(approval.get("role"), str) or approval["role"] not in ACTION_SCOPES[action]:
        _reject(ledger_path, approval, "role_scope_denied", instant)
    if (not _valid_principal(principal)
            or principal.subject != approval.get("approved_by")
            or principal.issuer != approval.get("approved_by_issuer")
            or approval.get("role") not in principal.roles):
        _reject(ledger_path, approval, "actor_principal_mismatch", instant)
    signature = approval.get("signature")
    try:
        expected = hmac.new(key, _signed_payload(approval), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        _reject(ledger_path, approval, "approval_signature_invalid", instant)
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
        **_event_identity(), "event_type": "dry_run_completed",
        "status": "simulated_execution", "case_id": approval["case_id"],
        "action": approval["action"], "target": approval["target"], "environment": environment,
        "approval_id": approval["approval_id"], "nonce_sha256": _nonce_sha256(approval["nonce"]),
        "approver_ref": hashlib.sha256((principal.issuer + "\0" + principal.subject).encode("utf-8")).hexdigest(),
        "investigation_run_id": approval["investigation_run_id"], "timestamp": _iso(instant),
        "external_system_calls": 0, "external_writes": 0,
        "simulation": simulated,
        "rollback_verification": {"status": "simulated_pass", "checks": checks,
            "real_environment_verified": False},
    }
    source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["op_run_manifest"] = build_op_run_manifest(
        project="OpsPilot", run_id=result["run_id"], evaluation_id="op4_remediation_dry_run",
        dataset_id="op4_investigation_report", dataset_sha256=_digest(investigation_report),
        truth_sha256=None, split="test_dry_run", evaluator_sha256=source_sha,
        result_fingerprint_sha256=_digest(result), git_sha=current_git_sha(ROOT), model_id="none",
        prompt_sha=None, tool_schema_sha=None, runtime_digest=None, max_steps=1, max_tokens=None,
        scorer_sha=source_sha, gold_source="not_applicable", cost_usd_estimate=0.0,
        trace_id=result["trace_id"], started_at=_iso(instant), ended_at=_iso(instant),
        parameters={"artifact_type": "action_dry_run", "case_id": approval["case_id"],
            "action": approval["action"], "target": approval["target"], "environment": environment,
            "approval_id": approval["approval_id"], "external_system_calls": 0, "external_writes": 0,
            "real_environment_verified": False, "span_id": result["span_id"]}, status="completed")
    manifest_errors = validate_op_run_artifact(result, is_new_run=True)
    if manifest_errors:
        raise ValueError("refusing to record unmanifested OP-4 dry run: " + "; ".join(manifest_errors))
    with _LEDGER_LOCK, _ledger_file_lock(ledger_path):
        events = _read_ledger_unlocked(ledger_path)
        if any(event.get("event_type") == "approval_revoked"
                and (_event_matches_nonce(event, approval["nonce"])
                     or event.get("approval_id") == approval["approval_id"])
                for event in events):
            _append_unlocked(ledger_path, {**_event_identity(), "event_type": "dry_run_rejected",
                "timestamp": _iso(instant), "approval_id": approval.get("approval_id"),
                "case_id": approval.get("case_id"), "reason": "approval_revoked",
                "external_system_calls": 0, "external_writes": 0})
            raise ActionRejected("approval_revoked")
        if any(event.get("event_type") == "dry_run_completed" and _event_matches_nonce(event, approval["nonce"]) for event in events):
            _append_unlocked(ledger_path, {**_event_identity(), "event_type": "dry_run_rejected",
                "timestamp": _iso(instant), "approval_id": approval.get("approval_id"),
                "case_id": approval.get("case_id"), "reason": "approval_replayed",
                "external_system_calls": 0, "external_writes": 0})
            raise ActionRejected("approval_replayed")
        _append_unlocked(ledger_path, result)
    return result
