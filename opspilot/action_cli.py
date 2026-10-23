"""Human-confirmed CLI for signed, test-only remediation dry runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .action_safety import ActionRejected, execute_dry_run, issue_approval, load_or_create_key


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
    approve.add_argument("--role", choices=("operator", "release_manager", "agent"), required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--ttl-seconds", type=int, default=120)
    dry_run = commands.add_parser("dry-run", help="consume an approval in the local simulator")
    dry_run.add_argument("--approval-file", type=Path, required=True)
    dry_run.add_argument("--environment", choices=("test", "production"), required=True)
    dry_run.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        key = load_or_create_key(KEY_PATH)
        if args.command == "approve":
            confirmation = input(f"Type APPROVE {args.case_id} to create a test-only dry-run token: ").strip()
            if confirmation != f"APPROVE {args.case_id}":
                raise ActionRejected("human_confirmation_mismatch")
            approval = issue_approval(case_id=args.case_id, action=args.action, target=args.target,
                environment=args.environment, role=args.role, approved_by=args.approved_by,
                key=key, ttl_seconds=args.ttl_seconds)
            APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
            path = APPROVAL_DIR / f"{approval['approval_id']}.json"
            path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"approval_id": approval["approval_id"], "expires_at": approval["expires_at"],
                "approval_file": str(path), "external_system_calls": 0}, ensure_ascii=False))
            return 0
        approval: dict[str, Any] = json.loads(args.approval_file.read_text(encoding="utf-8"))
        result = execute_dry_run(approval, key=key, environment=args.environment, ledger_path=AUDIT_PATH)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ActionRejected, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc), "external_system_calls": 0}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
