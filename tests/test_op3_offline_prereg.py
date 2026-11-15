from __future__ import annotations

import json
import unittest

from scripts.prepare_op3_offline_prereg import prepare_report
from scripts.prepare_op3_payload_review import prepare_payload_review


class OfflinePreregistrationTests(unittest.TestCase):
    def test_payload_review_is_offline_and_matches_authorized_plan_hashes(self):
        from pathlib import Path
        review = prepare_payload_review()
        root = Path(__file__).resolve().parents[1]
        plan = json.loads((root / "reports" /
            "op3-multiagent-retest-plan-2026-11-15.json").read_text(encoding="utf-8"))

        self.assertEqual(0, review["validation"]["provider_requests"])
        self.assertEqual(18, review["request_plan_max_provider_requests"])
        self.assertEqual(15, review["static_request_count"])
        self.assertEqual(3, review["dynamic_adjudicator_count"])
        self.assertEqual(plan["scope"]["snapshot_sha256"],
            {case["case_id"]: case["snapshot_sha256"] for case in review["cases"]})
        self.assertEqual(15, plan["prompt_review"]["static_request_hash_count"])

    def test_report_checks_authorized_snapshots_without_provider_calls_or_gold(self):
        report = prepare_report()

        self.assertEqual("prepared_provider_balance_pending", report["status"])
        self.assertEqual("not_performed", report["provider_balance_check"])
        self.assertEqual(0, report["inference_calls"])
        self.assertEqual(3, report["frozen_input"]["case_count"])
        self.assertEqual(18, report["request_plan"]["max_provider_requests"])
        self.assertEqual(18, report["budget"]["max_provider_requests"])
        self.assertTrue(report["budget"]["within_project_cap"])
        billing = report["provider_billing_reconciliation"]
        self.assertEqual("CNY", billing["currency"])
        self.assertEqual(10.91277486, billing["account_cost_cny"])
        self.assertEqual(10.91277486, billing["amount_csv_recalculated_total_cny"])
        self.assertEqual(3.79653708, billing["opspilot_key_derived_cost_cny"])
        self.assertEqual(1305, billing["opspilot_request_count"])
        self.assertEqual("complete", billing["export_window"]["coverage_status"])
        self.assertFalse(billing["account_and_key_totals_are_additive"])
        self.assertFalse(billing["usd_api_cost_ledger_modified"])
        self.assertFalse(billing["fx_conversion_applied"])
        self.assertEqual(["rules", "single", "multi", "auto"],
            [item["id"] for item in report["strategies"]])
        self.assertEqual("always-multi", report["strategies"][2]["label"])
        for case in report["frozen_input"]["cases"]:
            self.assertEqual(["deployments", "logs", "metrics", "traces"], case["sources"])

        def assert_no_gold_keys(value):
            if isinstance(value, dict):
                self.assertFalse({"root_causes", "gold_evidence_ids", "truth"} & value.keys())
                for child in value.values():
                    assert_no_gold_keys(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_gold_keys(child)

        assert_no_gold_keys(report)

    def test_offline_timeout_cancel_fixture_is_separate_from_experiment(self):
        report = prepare_report()
        fixture = report["offline_fault_metric_fixture"]
        self.assertEqual("metric_contract_test_fixture_not_experiment_result", fixture["status"])
        self.assertEqual(0, fixture["model_calls"])
        expected = {"timeouts": 1, "cancellations": 1}
        self.assertEqual({strategy: expected for strategy in ("rules", "single", "multi", "auto")},
            fixture["per_strategy_counts"])


if __name__ == "__main__":
    unittest.main()
