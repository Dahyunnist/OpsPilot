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
                   role: str, approved_by: str, key: bytes, ttl_seconds: int = 120,
                   now: datetime | None = None) -> dict[str, Any]:
    if not case_id or len(case_id) > 100:
        raise ActionRejected("invalid_case_id")
    if environment != ALLOWED_ENVIRONMENT:
        raise ActionRejected("environment_not_allowed")
    if action not in ACTION_SCOPES or target not in SERVICE_TARGETS:
        raise ActionRejected("action_or_target_not_allowed")
    if role not in ACTION_SCOPES[action]:
        raise ActionRejected("role_scope_denied")
    if not approved_by or len(approved_by) > 120:
        raise ActionRejected("approver_required")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 300:
        raise ActionRejected("approval_ttl_out_of_range")
    if len(key) != 32:
        raise ValueError("approval signing key must be exactly 32 bytes")
    issued = _time(now)
    approval = {
        "approval_id": str(uuid.uuid4()), "nonce": secrets.token_urlsafe(24),
        "case_id": case_id, "action": action, "target": target,
        "environment": environment, "role": role, "approved_by": approved_by,
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
                    ledger_path: Path, now: datetime | None = None) -> dict[str, Any]:
    instant = _time(now)
    if environment != ALLOWED_ENVIRONMENT or approval.get("environment") != environment:
        _reject(ledger_path, approval, "environment_mismatch", instant)
    if approval.get("action") not in ACTION_SCOPES or approval.get("target") not in SERVICE_TARGETS:
        _reject(ledger_path, approval, "action_or_target_not_allowed", instant)
    if approval.get("role") not in ACTION_SCOPES[approval["action"]]:
        _reject(ledger_path, approval, "role_scope_denied", instant)
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
    with _LEDGER_LOCK:
        if ledger_path.exists():
            try:
                events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except (OSError, json.JSONDecodeError):
                raise ActionRejected("action_ledger_unreadable")
            if any(event.get("event_type") == "dry_run_completed" and event.get("nonce") == approval["nonce"] for event in events):
                _append_unlocked(ledger_path, {"event_id": str(uuid.uuid4()), "event_type": "dry_run_rejected",
                    "timestamp": _iso(instant), "approval_id": approval.get("approval_id"),
                    "case_id": approval.get("case_id"), "reason": "approval_replayed", "external_system_calls": 0})
                raise ActionRejected("approval_replayed")
        _append_unlocked(ledger_path, result)
    return result
