from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import json
from pathlib import Path
import tempfile
import unittest

from opspilot.action_safety import ActionRejected, execute_dry_run, issue_approval, load_or_create_key


NOW = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)


class ActionSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = b"k" * 32
        self.ledger = self.root / "action-audit.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def approval(self, **overrides):
        values = {"case_id": "lab-release-01", "action": "rollback_release", "target": "orders",
            "environment": "test", "role": "release_manager", "approved_by": "human-operator",
            "key": self.key, "ttl_seconds": 120, "now": NOW}
        values.update(overrides)
        return issue_approval(**values)

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]

    def test_signed_rollback_is_test_only_and_reports_simulated_verification(self) -> None:
        result = execute_dry_run(self.approval(), key=self.key, environment="test", ledger_path=self.ledger, now=NOW)
        self.assertEqual(result["status"], "simulated_execution")
        self.assertEqual(result["external_system_calls"], 0)
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["simulation"]["rollback"], "simulated")
        self.assertFalse(result["rollback_verification"]["real_environment_verified"])

    def test_expired_approval_is_rejected_and_audited(self) -> None:
        result = self.approval()
        with self.assertRaisesRegex(ActionRejected, "approval_expired_or_invalid"):
            execute_dry_run(result, key=self.key, environment="test", ledger_path=self.ledger,
                now=NOW + timedelta(seconds=121))
        self.assertEqual(self.events()[0]["reason"], "approval_expired_or_invalid")

    def test_replayed_approval_is_rejected_after_first_consumption(self) -> None:
        approval = self.approval()
        execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger, now=NOW)
        with self.assertRaisesRegex(ActionRejected, "approval_replayed"):
            execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger, now=NOW)
        self.assertEqual([event["event_type"] for event in self.events()], ["dry_run_completed", "dry_run_rejected"])

    def test_wrong_environment_and_tampered_signature_are_rejected(self) -> None:
        approval = self.approval()
        with self.assertRaisesRegex(ActionRejected, "environment_mismatch"):
            execute_dry_run(approval, key=self.key, environment="production", ledger_path=self.ledger, now=NOW)
        altered = dict(approval, target="payment")
        with self.assertRaisesRegex(ActionRejected, "approval_signature_invalid"):
            execute_dry_run(altered, key=self.key, environment="test", ledger_path=self.ledger, now=NOW)
        self.assertEqual(len(self.events()), 2)
        self.assertTrue(all(event["external_system_calls"] == 0 for event in self.events()))

    def test_agent_or_wrong_role_cannot_escalate_action_scope(self) -> None:
        with self.assertRaisesRegex(ActionRejected, "role_scope_denied"):
            self.approval(role="agent")
        with self.assertRaisesRegex(ActionRejected, "role_scope_denied"):
            self.approval(role="operator")
        with self.assertRaisesRegex(ActionRejected, "environment_not_allowed"):
            self.approval(environment="production")

    def test_key_creation_is_stable_and_exactly_32_bytes(self) -> None:
        key_path = self.root / "private" / "approval.key"
        first = load_or_create_key(key_path)
        self.assertEqual(len(first), 32)
        self.assertEqual(first, load_or_create_key(key_path))

    def test_simulator_has_no_network_or_process_execution_dependency(self) -> None:
        source = Path(__file__).resolve().parents[1] / "opspilot" / "action_safety.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & {"socket", "urllib", "requests", "http", "subprocess", "docker", "psycopg", "redis"})


if __name__ == "__main__":
    unittest.main()
