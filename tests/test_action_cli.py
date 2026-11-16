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
            "query_trace": [{"source": "logs", "status": "ok", "params": {"limit": 100},
                "records": [{"record_id": "log-01"}]}],
            "recommendation": {"action_id": "rollback_order_config", "risk": "high",
                "requires_approval": True}}), encoding="utf-8")
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

    def assert_correlated_redacted(self, event: dict) -> None:
        self.assertTrue(event["run_id"])
        self.assertRegex(event["trace_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(event["span_id"], r"^[0-9a-f]{16}$")
        for key in ("nonce", "signature", "key", "confirmation"):
            self.assertNotIn(key, event)
        self.assertEqual(0, event["external_system_calls"])
        self.assertEqual(0, event["external_writes"])

    def test_wrong_human_confirmation_creates_no_approval(self) -> None:
        error_output = StringIO()
        supplied_text = "UNTRUSTED-CONFIRMATION-SECRET"
        with patch("builtins.input", return_value=supplied_text), redirect_stderr(error_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(2, result)
        self.assertEqual("human_confirmation_mismatch", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("approval_issue_rejected", event["event_type"])
        self.assertEqual("human_confirmation_mismatch", event["reason"])
        self.assertNotIn(supplied_text, json.dumps(event))
        self.assert_correlated_redacted(event)

    def test_missing_human_confirmation_is_audited_without_ticket(self) -> None:
        error_output = StringIO()
        with patch("builtins.input", return_value=""), redirect_stderr(error_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(2, result)
        self.assertEqual("human_confirmation_mismatch", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("approval_issue_rejected", event["event_type"])
        self.assertEqual("human_confirmation_mismatch", event["reason"])
        self.assert_correlated_redacted(event)

    def test_confirmed_cli_ticket_reaches_simulated_review_only(self) -> None:
        approval_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stdout(approval_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(0, result)
        approval_files = list(self.approvals.glob("*.json"))
        self.assertEqual(1, len(approval_files))
        approval = json.loads(approval_files[0].read_text(encoding="utf-8"))
        issued = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("approval_issued", issued["event_type"])
        self.assertEqual(approval["approval_id"], issued["approval_id"])
        self.assert_correlated_redacted(issued)
        for secret in (approval["nonce"], approval["signature"]):
            self.assertNotIn(secret, json.dumps(issued))

        dry_run_output = StringIO()
        with redirect_stdout(dry_run_output):
            result = action_cli.main(["dry-run", "--approval-file", str(approval_files[0]),
                                      "--investigation-report", str(self.report_path),
                                      "--environment", "test", "--dry-run", "--identity-mode", "local-test",
                                      "--actor-id", "local-release-manager"])
        self.assertEqual(0, result)
        response = json.loads(dry_run_output.getvalue())
        self.assertEqual(0, response["external_system_calls"])
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
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
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("dry_run_rejected", event["event_type"])
        self.assertEqual(0, event["external_system_calls"])
        self.assertEqual(0, event.get("external_writes", 0))

    def test_human_can_revoke_ticket_and_dry_run_then_fails_closed(self) -> None:
        approval_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stdout(approval_output):
            self.assertEqual(0, action_cli.main(self.approve_args()))
        approval_file = next(self.approvals.glob("*.json"))
        approval_id = json.loads(approval_file.read_text(encoding="utf-8"))["approval_id"]

        revoke_output = StringIO()
        with patch("builtins.input", return_value=f"REVOKE {approval_id}"), redirect_stdout(revoke_output):
            result = action_cli.main(["revoke", "--approval-file", str(approval_file),
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"])
        self.assertEqual(0, result)
        self.assertEqual("approval_revoked", json.loads(revoke_output.getvalue())["status"])

        error_output = StringIO()
        with redirect_stderr(error_output):
            result = action_cli.main(["dry-run", "--approval-file", str(approval_file),
                "--investigation-report", str(self.report_path), "--environment", "test", "--dry-run",
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"])
        self.assertEqual(2, result)
        self.assertEqual("approval_revoked", json.loads(error_output.getvalue())["reason"])
        events = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["approval_issued", "approval_revoked", "dry_run_rejected"],
            [event["event_type"] for event in events])
        self.assertTrue(all(event["external_system_calls"] == 0 for event in events))
        self.assertTrue(all(event.get("external_writes", 0) == 0 for event in events))

    def test_revoke_requires_exact_human_confirmation(self) -> None:
        approval_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stdout(approval_output):
            self.assertEqual(0, action_cli.main(self.approve_args()))
        approval_file = next(self.approvals.glob("*.json"))
        error_output = StringIO()
        supplied_text = "REVOKE UNTRUSTED-CONFIRMATION"
        with patch("builtins.input", return_value=supplied_text), redirect_stderr(error_output):
            result = action_cli.main(["revoke", "--approval-file", str(approval_file),
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"])
        self.assertEqual(2, result)
        self.assertEqual("human_revocation_confirmation_mismatch",
            json.loads(error_output.getvalue())["reason"])
        events = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["approval_issued", "approval_revoke_rejected"],
            [event["event_type"] for event in events])
        self.assertTrue(approval_file.exists())
        rejected = events[-1]
        self.assertEqual("human_revocation_confirmation_mismatch", rejected["reason"])
        self.assertNotIn(supplied_text, json.dumps(rejected))
        self.assert_correlated_redacted(rejected)
        self.assertFalse(any(event["event_type"] == "approval_revoked" for event in events))

    def test_unreadable_revoke_ticket_is_audited_without_reading_or_changing_ticket(self) -> None:
        ticket_path = self.root / "PRIVATE-TICKET.json"
        error_output = StringIO()
        with redirect_stderr(error_output):
            result = action_cli.main(["revoke", "--approval-file", str(ticket_path),
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"])
        self.assertEqual(2, result)
        self.assertEqual("approval_file_unreadable", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(ticket_path.exists())
        self.assertFalse((self.root / "approval.key").exists())
        missing_event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("approval_revoke_rejected", missing_event["event_type"])
        self.assertEqual("approval_file_unreadable", missing_event["reason"])
        self.assertNotIn(str(ticket_path), json.dumps(missing_event))
        self.assert_correlated_redacted(missing_event)

        ticket_contents = '{"signature":"PRIVATE-SIGNATURE","nonce":"PRIVATE-NONCE"'
        ticket_path.write_text(ticket_contents, encoding="utf-8")
        error_output = StringIO()
        with redirect_stderr(error_output):
            result = action_cli.main(["revoke", "--approval-file", str(ticket_path),
                "--identity-mode", "local-test", "--actor-id", "local-release-manager"])
        self.assertEqual(2, result)
        self.assertEqual("approval_file_unreadable", json.loads(error_output.getvalue())["reason"])
        self.assertEqual(ticket_contents, ticket_path.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "approval.key").exists())
        events = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["approval_revoke_rejected", "approval_revoke_rejected"],
            [event["event_type"] for event in events])
        self.assertEqual(missing_event["event_id"], events[0]["event_id"])
        serialized = json.dumps(events[1])
        for secret in (str(ticket_path), "PRIVATE-SIGNATURE", "PRIVATE-NONCE"):
            self.assertNotIn(secret, serialized)
        self.assert_correlated_redacted(events[1])

    def test_dry_run_requires_explicit_simulation_flag(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            action_cli.main(["dry-run", "--approval-file", str(self.root / "missing.json"),
                "--investigation-report", str(self.report_path), "--environment", "test"])
        self.assertEqual(2, raised.exception.code)
        self.assertFalse((self.root / "approval.key").exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("cli_arguments_rejected", event["event_type"])
        self.assertEqual("dry-run", event["command"])
        self.assertEqual("cli_arguments_invalid", event["reason"])
        self.assert_correlated_redacted(event)

    def test_approval_fails_closed_without_resolver(self) -> None:
        error_output = StringIO()
        with redirect_stderr(error_output):
            result = action_cli.main([arg for arg in self.approve_args()
                if arg not in ("--identity-mode", "local-test", "--actor-id", "local-release-manager")])
        self.assertEqual(2, result)
        self.assertEqual("actor_resolver_unconfigured", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("approval_issue_rejected", event["event_type"])
        self.assertEqual("actor_resolver_unconfigured", event["reason"])
        self.assert_correlated_redacted(event)

    def test_local_resolver_rejects_actor_without_required_role(self) -> None:
        error_output = StringIO()
        args = [item for item in self.approve_args() if item not in ("--actor-id", "local-release-manager")]
        args.extend(["--actor-id", "local-operator"])
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stderr(error_output):
            result = action_cli.main(args)
        self.assertEqual(2, result)
        self.assertEqual("role_scope_denied", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("role_scope_denied", event["reason"])
        self.assert_correlated_redacted(event)

    def test_unknown_resolver_identity_is_audited_without_identity_value(self) -> None:
        error_output = StringIO()
        unknown_actor = "private-unrecognized-actor"
        args = [item for item in self.approve_args()
            if item not in ("--actor-id", "local-release-manager")]
        args.extend(["--actor-id", unknown_actor])
        with redirect_stderr(error_output):
            result = action_cli.main(args)
        self.assertEqual(2, result)
        self.assertEqual("actor_identity_unverified", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("actor_identity_unverified", event["reason"])
        self.assertNotIn(unknown_actor, json.dumps(event))
        self.assert_correlated_redacted(event)

    def test_unreadable_investigation_report_is_audited_with_fixed_code(self) -> None:
        self.report_path.unlink()
        error_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stderr(error_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(2, result)
        self.assertEqual("investigation_report_unreadable", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        self.assertFalse((self.root / "approval.key").exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("approval_issue_rejected", event["event_type"])
        self.assertEqual("investigation_report_unreadable", event["reason"])
        self.assertNotIn(str(self.report_path), json.dumps(event))
        self.assert_correlated_redacted(event)

    def test_missing_investigation_run_binding_is_audited_without_report_content(self) -> None:
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        report.pop("run_id")
        report["private_notes"] = "PRIVATE-INVESTIGATION-REPORT-BODY"
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        error_output = StringIO()
        with patch("builtins.input", return_value="APPROVE case-01"), redirect_stderr(error_output):
            result = action_cli.main(self.approve_args())
        self.assertEqual(2, result)
        self.assertEqual("investigation_run_id_missing", json.loads(error_output.getvalue())["reason"])
        self.assertFalse(self.approvals.exists())
        self.assertFalse((self.root / "approval.key").exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("approval_issue_rejected", event["event_type"])
        self.assertEqual("investigation_run_id_missing", event["reason"])
        self.assertNotIn("PRIVATE-INVESTIGATION-REPORT-BODY", json.dumps(event))
        self.assertNotIn("investigation_run_id", event)
        self.assert_correlated_redacted(event)

    def test_request_cannot_supply_release_manager_role(self) -> None:
        args = self.approve_args() + ["--role", "release_manager"]
        with self.assertRaises(SystemExit) as raised:
            action_cli.main(args)
        self.assertEqual(2, raised.exception.code)
        self.assertFalse(self.approvals.exists())
        self.assertFalse((self.root / "approval.key").exists())
        event = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("cli_arguments_rejected", event["event_type"])
        self.assertEqual("approve", event["command"])
        self.assertEqual("cli_arguments_invalid", event["reason"])
        self.assertNotIn("--role", json.dumps(event))
        self.assert_correlated_redacted(event)


if __name__ == "__main__":
    unittest.main()
