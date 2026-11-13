from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from opspilot.model_client import ChatModel
from opspilot.run_manifest import validate_op_run_artifact
from scripts.evaluate_s15_mock_ablation import (
    ROOT, STRATEGIES, _candidate_predictions, build_s15_op_manifest, evaluate,
)


class S15MockAblationTests(unittest.TestCase):
    def test_frozen_paired_run_is_deterministic_and_never_calls_model(self):
        with patch.object(ChatModel, "complete", side_effect=AssertionError("model calls forbidden")):
            first = evaluate()
            second = evaluate()
        self.assertEqual(30, first["case_count_per_strategy"])
        self.assertEqual(list(STRATEGIES), first["strategies"])
        self.assertEqual(first["benchmark"]["manifest_sha256"], second["benchmark"]["manifest_sha256"])
        self.assertEqual(first["benchmark"]["data_sha256"], second["benchmark"]["data_sha256"])
        self.assertEqual(first["benchmark"]["labels_sha256"], second["benchmark"]["labels_sha256"])
        self.assertEqual(first["deterministic_result_sha256"], second["deterministic_result_sha256"])
        self.assertEqual({"evaluator", "investigator", "routing", "incident_loader"}, set(first["code_sha256"]))
        self.assertTrue(all(len(value) == 64 for value in first["code_sha256"].values()))
        self.assertEqual("test", first["parameters"]["split"])
        self.assertEqual(100, first["parameters"]["max_rows_per_source"])
        self.assertEqual(8, first["parameters"]["query_budget"])
        self.assertEqual(list(STRATEGIES), first["parameters"]["strategies"])
        self.assertEqual({"rules", "single", "always-multi", "auto"}, set(first["parameters"]["mock_policies"]))
        paired_ids = [case["incident_id"] for case in first["cases"]["rules"]]
        for strategy in STRATEGIES:
            left, right = first["cases"][strategy], second["cases"][strategy]
            self.assertEqual([case["incident_id"] for case in left], [case["incident_id"] for case in right])
            self.assertEqual(paired_ids, [case["incident_id"] for case in left])
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

    def test_actual_mock_evaluation_flows_into_valid_shared_manifest(self):
        with patch.object(ChatModel, "complete", side_effect=AssertionError("model calls forbidden")):
            result = evaluate()
        manifest = build_s15_op_manifest(
            result, started_at="2026-11-13T01:00:00+00:00", ended_at="2026-11-13T01:01:00+00:00",
            run_id="synthetic-run-01", trace_id="synthetic-trace-01", git_sha="a" * 40,
        )
        self.assertEqual([], validate_op_run_artifact({"op_run_manifest": manifest}, is_new_run=True))
        self.assertEqual(result["benchmark"]["manifest"]["benchmark_id"], manifest["dataset_id"])
        self.assertEqual(result["benchmark"]["data_sha256"], manifest["dataset_sha256"])
        self.assertEqual(result["benchmark"]["labels_sha256"], manifest["truth_sha256"])
        self.assertEqual(result["deterministic_result_sha256"], manifest["result_fingerprint_sha256"])
        self.assertEqual(result["parameters"], manifest["parameters"])
        self.assertEqual("none", manifest["model_id"])
        self.assertEqual(0.0, manifest["cost_usd_estimate"])

    def test_unknown_fault_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "known strategy"):
            evaluate(injected_faults={("rules", "not-in-frozen-split"): "cancel"})

    def test_modified_manifest_or_truth_file_is_rejected_before_scoring(self):
        source = ROOT / "data" / "causal-benchmark"
        for modified in ("manifest.json", "labels/test.json"):
            with self.subTest(modified=modified), tempfile.TemporaryDirectory() as temporary:
                benchmark = Path(temporary)
                (benchmark / "labels").mkdir()
                for relative in ("manifest.json", "test.jsonl", "labels/test.json"):
                    shutil.copyfile(source / relative, benchmark / relative)
                tampered = benchmark / modified
                tampered.write_bytes(tampered.read_bytes() + b"\n")
                with self.assertRaisesRegex(ValueError, "frozen S15 test inputs changed"):
                    evaluate(benchmark)

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
