from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import json
from pathlib import Path
import tempfile
import unittest

from opspilot.action_safety import ActionRejected, approval_binding, execute_dry_run, issue_approval, load_or_create_key
from opspilot.identity import Principal


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
        role = values.pop("role")
        values.pop("approved_by")
        values["principal"] = Principal("human-operator", frozenset({role}), "test", True)
        report = values.pop("investigation_report", self.report())
        binding = approval_binding(report, action=values["action"], target=values["target"],
            environment=values["environment"])
        values.setdefault("investigation_run_id", binding["investigation_run_id"])
        values.setdefault("evidence_sha256", binding["evidence_sha256"])
        values.setdefault("recommendation_sha256", binding["recommendation_sha256"])
        return issue_approval(**values)

    def report(self):
        return {"incident_id": "lab-release-01", "run_id": "run-01",
            "evidence_refs": [{"source": "logs", "record_id": "log-01"}],
            "query_trace": [{"source": "deployments", "status": "ok", "params": {"limit": 100},
                "records": [{"record_id": "deploy-01"}]}],
            "recommendation": {"action_id": "rollback_order_config", "risk": "high"}}

    def principal(self):
        return Principal("human-operator", frozenset({"operator", "release_manager"}), "test", True)

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]

    def test_signed_rollback_is_test_only_and_reports_simulated_verification(self) -> None:
        result = execute_dry_run(self.approval(), key=self.key, environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertEqual(result["status"], "simulated_execution")
        self.assertEqual(result["external_system_calls"], 0)
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["simulation"]["rollback"], "simulated")
        self.assertFalse(result["rollback_verification"]["real_environment_verified"])

    def test_expired_approval_is_rejected_and_audited(self) -> None:
        result = self.approval()
        with self.assertRaisesRegex(ActionRejected, "approval_expired_or_invalid"):
            execute_dry_run(result, key=self.key, environment="test", ledger_path=self.ledger,
                investigation_report=self.report(), principal=self.principal(), now=NOW + timedelta(seconds=121))
        self.assertEqual(self.events()[0]["reason"], "approval_expired_or_invalid")
        self.assertEqual(0, self.events()[0]["external_system_calls"])

    def test_approval_expires_at_exact_boundary(self) -> None:
        approval = self.approval(ttl_seconds=120)
        with self.assertRaisesRegex(ActionRejected, "approval_expired_or_invalid"):
            execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger,
                investigation_report=self.report(), principal=self.principal(), now=NOW + timedelta(seconds=120))
        self.assertEqual("approval_expired_or_invalid", self.events()[0]["reason"])
        self.assertEqual(0, self.events()[0]["external_system_calls"])

    def test_replayed_approval_is_rejected_after_first_consumption(self) -> None:
        approval = self.approval()
        execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        with self.assertRaisesRegex(ActionRejected, "approval_replayed"):
            execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertEqual([event["event_type"] for event in self.events()], ["dry_run_completed", "dry_run_rejected"])
        self.assertTrue(all(event["external_system_calls"] == 0 for event in self.events()))

    def test_wrong_environment_and_tampered_signature_are_rejected(self) -> None:
        approval = self.approval()
        with self.assertRaisesRegex(ActionRejected, "environment_mismatch"):
            execute_dry_run(approval, key=self.key, environment="production", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        altered = dict(approval, target="payment")
        with self.assertRaisesRegex(ActionRejected, "approval_signature_invalid"):
            execute_dry_run(altered, key=self.key, environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertEqual(len(self.events()), 2)
        self.assertTrue(all(event["external_system_calls"] == 0 for event in self.events()))

    def test_approval_is_bound_to_exact_action_and_target(self) -> None:
        approval = self.approval(action="restart_service", target="orders", role="operator")
        altered_action = dict(approval, action="rollback_release", role="release_manager")
        altered_target = dict(approval, target="payment")
        for field, altered in (("action", altered_action), ("target", altered_target)):
            with self.subTest(field=field), self.assertRaisesRegex(ActionRejected, "approval_signature_invalid"):
                execute_dry_run(altered, key=self.key, environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertTrue(all(event["external_system_calls"] == 0 for event in self.events()))

    def test_recommendation_signed_approval_dry_run_audit_chain(self) -> None:
        recommendation = {"case_id": "lab-release-01", "action": "rollback_release", "target": "orders"}
        approval = self.approval(case_id=recommendation["case_id"], action=recommendation["action"],
            target=recommendation["target"], role="release_manager")
        self.assertEqual((recommendation["action"], recommendation["target"]), (approval["action"], approval["target"]))
        result = execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertEqual("simulated_execution", result["status"])
        self.assertFalse(result["rollback_verification"]["real_environment_verified"])
        self.assertEqual(0, result["external_system_calls"])
        self.assertEqual(0, result["external_writes"])
        self.assertEqual("dry_run_completed", self.events()[0]["event_type"])
        self.assertEqual("simulated_execution", self.events()[0]["status"])
        self.assertEqual(approval["approval_id"], self.events()[0]["approval_id"])

    def test_changed_run_evidence_or_recommendation_rejects_old_approval(self) -> None:
        approval = self.approval()
        variants = []
        changed_evidence = self.report()
        changed_evidence["evidence_refs"][0]["record_id"] = "log-02"
        variants.append(changed_evidence)
        changed_recommendation = self.report()
        changed_recommendation["recommendation"]["risk"] = "critical"
        variants.append(changed_recommendation)
        changed_run = self.report()
        changed_run["run_id"] = "run-02"
        variants.append(changed_run)
        for report in variants:
            with self.subTest(report=report), self.assertRaisesRegex(ActionRejected, "approval_investigation_mismatch"):
                execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger,
                    investigation_report=report, principal=self.principal(), now=NOW)
        self.assertEqual(["approval_investigation_mismatch"] * 3,
            [event["reason"] for event in self.events()])
        self.assertTrue(all(event["external_system_calls"] == 0 for event in self.events()))
        self.assertTrue(all(event.get("external_writes", 0) == 0 for event in self.events()))

    def test_agent_or_wrong_role_cannot_escalate_action_scope(self) -> None:
        with self.assertRaisesRegex(ActionRejected, "role_scope_denied"):
            self.approval(role="agent")
        with self.assertRaisesRegex(ActionRejected, "role_scope_denied"):
            self.approval(role="operator")
        with self.assertRaisesRegex(ActionRejected, "environment_not_allowed"):
            self.approval(environment="production")

    def test_role_action_matrix_rejects_every_out_of_scope_pair(self) -> None:
        pairs = (("operator", "rollback_release"), ("release_manager", "restart_service"),
                 ("agent", "restart_service"), ("agent", "rollback_release"))
        for role, action in pairs:
            with self.subTest(role=role, action=action), self.assertRaisesRegex(ActionRejected, "role_scope_denied"):
                self.approval(role=role, action=action)

    def test_signed_approval_cannot_claim_production_or_agent_scope(self) -> None:
        approval = self.approval()
        with self.assertRaisesRegex(ActionRejected, "environment_mismatch"):
            execute_dry_run(dict(approval, environment="production"), key=self.key, environment="test",
                ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        with self.assertRaisesRegex(ActionRejected, "role_scope_denied"):
            execute_dry_run(dict(approval, role="agent"), key=self.key, environment="test",
                ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertEqual(["environment_mismatch", "role_scope_denied"], [event["reason"] for event in self.events()])
        self.assertTrue(all(event["external_system_calls"] == 0 for event in self.events()))

    def test_different_principal_cannot_consume_approval(self) -> None:
        approval = self.approval()
        impostor = Principal("other-human", frozenset({"release_manager"}), "test", True)
        with self.assertRaisesRegex(ActionRejected, "actor_principal_mismatch"):
            execute_dry_run(approval, key=self.key, environment="test", ledger_path=self.ledger,
                investigation_report=self.report(), principal=impostor, now=NOW)
        self.assertEqual("actor_principal_mismatch", self.events()[0]["reason"])
        self.assertEqual(0, self.events()[0]["external_system_calls"])

    def test_key_creation_is_stable_and_exactly_32_bytes(self) -> None:
        key_path = self.root / "private" / "approval.key"
        first = load_or_create_key(key_path)
        self.assertEqual(len(first), 32)
        self.assertEqual(first, load_or_create_key(key_path))

    def test_executor_rejects_short_signing_key_before_audit_or_execution(self) -> None:
        approval = self.approval()
        with self.assertRaisesRegex(ValueError, "approval signing key must be exactly 32 bytes"):
            execute_dry_run(approval, key=b"short", environment="test", ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertFalse(self.ledger.exists())

    def test_corrupt_ledger_fails_closed_without_overwriting_it(self) -> None:
        corrupted = b'{"event_type":"incomplete"\n'
        self.ledger.write_bytes(corrupted)
        with self.assertRaisesRegex(ActionRejected, "action_ledger_unreadable"):
            execute_dry_run(self.approval(), key=self.key, environment="test",
                ledger_path=self.ledger, investigation_report=self.report(), principal=self.principal(), now=NOW)
        self.assertEqual(self.ledger.read_bytes(), corrupted)

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
