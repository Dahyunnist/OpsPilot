"""Human-confirmed CLI for signed, test-only remediation dry runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .action_safety import (ActionRejected, approval_binding, execute_dry_run, issue_approval,
    load_or_create_key, record_approval_issued, record_approval_issue_rejected,
    record_approval_revoke_rejected, record_cli_arguments_rejected, revoke_approval)
from .identity import LocalTestActorResolver, resolve_principal


ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / "var" / "action-approval.key"
APPROVAL_DIR = ROOT / "var" / "action-approvals"
AUDIT_PATH = ROOT / "var" / "action-audit.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or simulate local test-only action approvals")
    commands = parser.add_subparsers(dest="command", required=True)
    approve = commands.add_parser("approve", help="create a signed, expiring human approval")
    approve.add_argument("--case-id", required=True)
    approve.add_argument("--action", choices=("restart_service", "rollback_release"), required=True)
    approve.add_argument("--target", choices=("gateway", "orders", "inventory", "payment"), required=True)
    approve.add_argument("--environment", choices=("test", "production"), required=True)
    approve.add_argument("--identity-mode", choices=("local-test",))
    approve.add_argument("--actor-id")
    approve.add_argument("--ttl-seconds", type=int, default=120)
    approve.add_argument("--investigation-report", type=Path, required=True)
    dry_run = commands.add_parser("dry-run", help="consume an approval in the local simulator")
    dry_run.add_argument("--approval-file", type=Path, required=True)
    dry_run.add_argument("--investigation-report", type=Path, required=True)
    dry_run.add_argument("--environment", choices=("test", "production"), required=True)
    dry_run.add_argument("--dry-run", action="store_true", required=True)
    dry_run.add_argument("--identity-mode", choices=("local-test",))
    dry_run.add_argument("--actor-id")
    revoke = commands.add_parser("revoke", help="revoke an unused signed approval")
    revoke.add_argument("--approval-file", type=Path, required=True)
    revoke.add_argument("--identity-mode", choices=("local-test",))
    revoke.add_argument("--actor-id")
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        if exc.code != 0 and raw_argv and raw_argv[0] in {"approve", "dry-run", "revoke"}:
            record_cli_arguments_rejected(raw_argv[0], ledger_path=AUDIT_PATH)
        raise
    try:
        resolver = LocalTestActorResolver() if args.identity_mode == "local-test" else None
        principal = resolve_principal(resolver, args.actor_id)
        if args.command == "revoke":
            try:
                approval: Any = json.loads(args.approval_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                record_approval_revoke_rejected(None, ledger_path=AUDIT_PATH,
                    reason="approval_file_unreadable")
                raise ActionRejected("approval_file_unreadable") from None
            if not isinstance(approval, dict):
                record_approval_revoke_rejected(None, ledger_path=AUDIT_PATH,
                    reason="approval_file_unreadable")
                raise ActionRejected("approval_file_unreadable")
            confirmation = input(f"Type REVOKE {approval.get('approval_id', '')} to revoke this approval: ").strip()
            if confirmation != f"REVOKE {approval.get('approval_id', '')}":
                record_approval_revoke_rejected(approval, ledger_path=AUDIT_PATH)
                raise ActionRejected("human_revocation_confirmation_mismatch")
            key = load_or_create_key(KEY_PATH)
            result = revoke_approval(approval, key=key, ledger_path=AUDIT_PATH, principal=principal)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "approve":
            try:
                investigation_report = json.loads(args.investigation_report.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                record_approval_issue_rejected(ledger_path=AUDIT_PATH, case_id=args.case_id,
                    action=args.action, target=args.target, environment=args.environment,
                    reason="investigation_report_unreadable")
                raise ActionRejected("investigation_report_unreadable") from None
            try:
                binding = approval_binding(investigation_report, action=args.action,
                    target=args.target, environment=args.environment)
            except ActionRejected as exc:
                record_approval_issue_rejected(ledger_path=AUDIT_PATH, case_id=args.case_id,
                    action=args.action, target=args.target, environment=args.environment,
                    reason=exc.code)
                raise
            if binding["case_id"] != args.case_id:
                record_approval_issue_rejected(ledger_path=AUDIT_PATH, case_id=args.case_id,
                    action=args.action, target=args.target, environment=args.environment,
                    reason="investigation_case_mismatch")
                raise ActionRejected("investigation_case_mismatch")
            confirmation = input(f"Type APPROVE {args.case_id} to create a test-only dry-run token: ").strip()
            if confirmation != f"APPROVE {args.case_id}":
                record_approval_issue_rejected(ledger_path=AUDIT_PATH, case_id=args.case_id,
                    action=args.action, target=args.target, environment=args.environment)
                raise ActionRejected("human_confirmation_mismatch")
            key = load_or_create_key(KEY_PATH)
            approval = issue_approval(case_id=args.case_id, action=args.action, target=args.target,
                environment=args.environment, principal=principal,
                investigation_run_id=binding["investigation_run_id"],
                evidence_sha256=binding["evidence_sha256"],
                recommendation_sha256=binding["recommendation_sha256"],
                key=key, ttl_seconds=args.ttl_seconds)
            APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
            path = APPROVAL_DIR / f"{approval['approval_id']}.json"
            path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                record_approval_issued(approval, ledger_path=AUDIT_PATH)
            except OSError:
                path.unlink(missing_ok=True)
                raise
            print(json.dumps({"approval_id": approval["approval_id"], "expires_at": approval["expires_at"],
                "approval_file": str(path), "external_system_calls": 0}, ensure_ascii=False))
            return 0
        key = load_or_create_key(KEY_PATH)
        approval: dict[str, Any] = json.loads(args.approval_file.read_text(encoding="utf-8"))
        investigation_report = json.loads(args.investigation_report.read_text(encoding="utf-8"))
        result = execute_dry_run(approval, key=key, environment=args.environment,
            ledger_path=AUDIT_PATH, investigation_report=investigation_report, principal=principal)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ActionRejected, OSError, ValueError, json.JSONDecodeError) as exc:
        issue_rejection_codes = {"actor_resolver_unconfigured", "actor_identity_required",
            "actor_identity_unverified", "role_scope_denied"}
        if args.command == "approve" and str(exc) in issue_rejection_codes:
            record_approval_issue_rejected(ledger_path=AUDIT_PATH, case_id=args.case_id,
                action=args.action, target=args.target, environment=args.environment, reason=str(exc))
        print(json.dumps({"status": "rejected", "reason": str(exc), "external_system_calls": 0}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
