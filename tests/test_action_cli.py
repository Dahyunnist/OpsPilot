from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opspilot import action_cli


class ActionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.approvals = self.root / "approvals"
        self.ledger = self.root / "audit.jsonl"
        self.report_path = self.root / "investigation.json"
        self.report_path.write_text(json.dumps({"incident_id": "case-01", "run_id": "run-01",
            "evidence_refs": [{"source": "logs", "record_id": "log-01"}],
            "recommendation": {"action_id": "rollback_order_config", "risk": "high"}}), encoding="utf-8")
        self.patches = [
            patch.object(action_cli, "KEY_PATH", self.root / "approval.key"),
            patch.object(action_cli, "APPROVAL_DIR", self.approvals),
            patch.object(action_cli, "AUDIT_PATH", self.ledger),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def approve_args(self) -> list[str]:
        return ["approve", "--case-id", "case-01", "--action", "rollback_release", "--target", "orders",
                "--environment", "test", "--investigation-report", str(self.report_path),
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"]

    def test_wrong_human_confirmation_creates_no_approval(self) -> None:
        error_output = StringIO()
        with patch("builtins.input", return_value="approve case-01"), redirect_stderr(error_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(2, result)
        self.assertEqual("human_confirmation_mismatch", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        self.assertFalse(self.ledger.exists())

    def test_confirmed_cli_ticket_reaches_simulated_review_only(self) -> None:
        approval_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stdout(approval_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(0, result)
        approval_files = list(self.approvals.glob("*.json"))
        self.assertEqual(1, len(approval_files))

        dry_run_output = StringIO()
        with redirect_stdout(dry_run_output):
            result = action_cli.main(["dry-run", "--approval-file", str(approval_files[0]),
                                      "--investigation-report", str(self.report_path),
                                      "--environment", "test", "--dry-run", "--identity-mode", "local-test",
                                      "--actor-id", "local-release-manager"])
        self.assertEqual(0, result)
        response = json.loads(dry_run_output.getvalue())
        self.assertEqual(0, response["external_system_calls"])
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("dry_run_completed", event["event_type"])
        self.assertEqual("simulated_execution", event["status"])
        self.assertEqual("simulated_pass", event["rollback_verification"]["status"])
        self.assertFalse(event["rollback_verification"]["real_environment_verified"])
        self.assertEqual(0, event["external_system_calls"])
        self.assertEqual(0, event["external_writes"])

    def test_changed_investigation_report_rejects_old_ticket(self) -> None:
        approval_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stdout(approval_output):
            self.assertEqual(0, action_cli.main(self.approve_args()))
        approval_file = next(self.approvals.glob("*.json"))
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        report["recommendation"]["risk"] = "critical"
        self.report_path.write_text(json.dumps(report), encoding="utf-8")

        error_output = StringIO()
        with redirect_stderr(error_output):
            result = action_cli.main(["dry-run", "--approval-file", str(approval_file),
                "--investigation-report", str(self.report_path), "--environment", "test", "--dry-run",
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"])
        self.assertEqual(2, result)
        self.assertEqual("approval_investigation_mismatch", json.loads(error_output.getvalue())["reason"])
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("dry_run_rejected", event["event_type"])
        self.assertEqual(0, event["external_system_calls"])
        self.assertEqual(0, event.get("external_writes", 0))

    def test_approval_fails_closed_without_resolver(self) -> None:
        error_output = StringIO()
        with redirect_stderr(error_output):
            result = action_cli.main([arg for arg in self.approve_args()
                if arg not in ("--identity-mode", "local-test", "--actor-id", "local-release-manager")])
        self.assertEqual(2, result)
        self.assertEqual("actor_resolver_unconfigured", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())

    def test_local_resolver_rejects_actor_without_required_role(self) -> None:
        error_output = StringIO()
        args = [item for item in self.approve_args() if item not in ("--actor-id", "local-release-manager")]
        args.extend(["--actor-id", "local-operator"])
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stderr(error_output):
            result = action_cli.main(args)
        self.assertEqual(2, result)
        self.assertEqual("role_scope_denied", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())

    def test_request_cannot_supply_release_manager_role(self) -> None:
        args = self.approve_args() + ["--role", "release_manager"]
        with self.assertRaises(SystemExit) as raised:
            action_cli.main(args)
        self.assertEqual(2, raised.exception.code)
        self.assertFalse(self.approvals.exists())


if __name__ == "__main__":
    unittest.main()
