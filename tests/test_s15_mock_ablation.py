from __future__ import annotations

import unittest
from unittest.mock import patch

from opspilot.model_client import ChatModel
from scripts.evaluate_s15_mock_ablation import STRATEGIES, _candidate_predictions, evaluate


class S15MockAblationTests(unittest.TestCase):
    def test_frozen_paired_run_is_deterministic_and_never_calls_model(self):
        with patch.object(ChatModel, "complete", side_effect=AssertionError("model calls forbidden")):
            first = evaluate()
            second = evaluate()
        self.assertEqual(30, first["case_count_per_strategy"])
        self.assertEqual(list(STRATEGIES), first["strategies"])
        self.assertEqual(first["benchmark"]["data_sha256"], second["benchmark"]["data_sha256"])
        for strategy in STRATEGIES:
            left, right = first["cases"][strategy], second["cases"][strategy]
            self.assertEqual([case["incident_id"] for case in left], [case["incident_id"] for case in right])
            self.assertEqual([case["prediction"] for case in left], [case["prediction"] for case in right])
            summary = first["summaries"][strategy]
            self.assertEqual(0, summary["model_calls"])
            self.assertEqual(0.0, summary["estimated_cost_usd"])
            self.assertEqual(0, summary["cancellations"])
            self.assertEqual(0, summary["failures"])
        rules_by_id = {case["incident_id"]: case["prediction"] for case in first["cases"]["rules"]}
        for case in first["cases"]["auto"]:
            if case["selected_route"] == "rules":
                self.assertEqual(rules_by_id[case["incident_id"]], case["prediction"])

    def test_injected_cancel_and_failure_are_counted_and_exported(self):
        baseline = evaluate()
        case_id = baseline["cases"]["rules"][0]["incident_id"]
        result = evaluate(injected_faults={("rules", case_id): "cancel", ("auto", case_id): "failure"})
        self.assertEqual(1, result["summaries"]["rules"]["cancellations"])
        self.assertEqual(1, result["summaries"]["auto"]["failures"])
        exported = {(item["strategy"], item["incident_id"], item["status"]) for item in result["failure_samples"]}
        self.assertIn(("rules", case_id, "cancelled"), exported)
        self.assertIn(("auto", case_id, "failed"), exported)

    def test_metrics_declare_mock_limits_and_independent_truth(self):
        result = evaluate()
        self.assertEqual("deterministic_mock_no_model_calls", result["runner"])
        self.assertIn("Mock predictions are not Agent outputs", result["interpretation"])
        self.assertIn("false_positives", result["metric_definitions"])
        self.assertIn("false_certainty", result["metric_definitions"])
        self.assertEqual(set(result["cases"]["rules"][0]["truth"].keys()) & {"active_causes"}, {"active_causes"})
        self.assertNotIn("active_causes", result["cases"]["rules"][0]["prediction"])

    def test_unknown_fault_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "known strategy"):
            evaluate(injected_faults={("rules", "not-in-frozen-split"): "cancel"})

    def test_single_mock_cites_only_selected_candidate_evidence(self):
        report = {"hypotheses": [
            {"hypothesis_id": "first", "strong_support": ["a"]},
            {"hypothesis_id": "second", "strong_support": ["b"]},
        ]}
        causes, evidence = _candidate_predictions(report, {"a", "b"}, 1)
        self.assertEqual(["first"], causes)
        self.assertEqual(["a"], evidence)


if __name__ == "__main__":
    unittest.main()
