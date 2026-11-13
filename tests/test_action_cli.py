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
                "--environment", "test", "--role", "release_manager", "--approved-by", "human-1"]

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
                                      "--environment", "test", "--dry-run"])
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


if __name__ == "__main__":
    unittest.main()
