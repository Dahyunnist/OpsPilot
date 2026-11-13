from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from opspilot.model_client import ChatModel
from opspilot.cli import load_incidents
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

    def test_frozen_mechanisms_route_safely_and_exhausted_query_budget_abstains(self):
        labels_path = ROOT / "data" / "causal-benchmark" / "labels" / "test.json"
        labels = {row["incident_id"]: row for row in json.loads(labels_path.read_text(encoding="utf-8"))["labels"]}
        incidents = load_incidents(ROOT / "data" / "causal-benchmark" / "test.jsonl")
        with patch.object(ChatModel, "complete", side_effect=AssertionError("model calls forbidden")):
            full = evaluate()
            exhausted = evaluate(query_budget=2)

        auto = {row["incident_id"]: row for row in full["cases"]["auto"]}
        by_family = {family: [row for incident_id, row in auto.items()
            if labels[incident_id]["scenario_family"] == family] for family in {
                "pool_root", "configuration_root_with_downstream_symptoms",
                "downstream_root_with_pool_pressure", "independent_dual_fault", "insufficient_or_conflicted"}}
        self.assertTrue(all(row["selected_route"] == "rules" for row in by_family["pool_root"]))
        self.assertTrue(all(row["selected_route"] == "rules" for row in by_family["configuration_root_with_downstream_symptoms"]))
        self.assertTrue(all(row["selected_route"] == "single-agent" for row in by_family["downstream_root_with_pool_pressure"]))
        self.assertTrue(all(row["selected_route"] == "multi-agent" for row in by_family["independent_dual_fault"]))
        self.assertTrue(all(row["selected_route"] == "rules" and not row["prediction"]["root_causes"]
                            for row in by_family["insufficient_or_conflicted"]))
        expected_route = {
            "pool_root": "rules", "configuration_root_with_downstream_symptoms": "rules",
            "downstream_root_with_pool_pressure": "single-agent", "independent_dual_fault": "multi-agent",
            "insufficient_or_conflicted": "rules",
        }
        for incident_id, row in auto.items():
            family = labels[incident_id]["scenario_family"]
            available = {record["record_id"] for records in incidents[incident_id]["signals"].values() for record in records}
            self.assertEqual("completed", row["status"], incident_id)
            self.assertEqual(expected_route[family], row["selected_route"], incident_id)
            self.assertFalse(row["query_budget_exhausted"], incident_id)
            self.assertLessEqual(row["query_count"], 8, incident_id)
            self.assertLessEqual(set(row["prediction"]["evidence_ids"]), available, incident_id)
            if not labels[incident_id]["decidable"]:
                self.assertEqual([], row["prediction"]["root_causes"], incident_id)
        self.assertEqual(0, full["summaries"]["auto"]["model_calls"])
        self.assertEqual(0.0, full["summaries"]["auto"]["estimated_cost_usd"])
        self.assertEqual({"correct": 18, "total": 24, "rate": 0.75},
                         full["summaries"]["auto"]["root_cause_set_exact"])
        self.assertEqual(6, full["summaries"]["auto"]["false_positives"]["causes"])

        exhausted_auto = exhausted["cases"]["auto"]
        self.assertTrue(all(row["selected_route"] == "rules" and not row["prediction"]["root_causes"]
                            for row in exhausted_auto))
        self.assertTrue(all(row["query_budget_exhausted"] and row["query_count"] == 2 for row in exhausted_auto))
        self.assertEqual(0, exhausted["summaries"]["auto"]["model_calls"])
        self.assertEqual(0.0, exhausted["summaries"]["auto"]["estimated_cost_usd"])
        manifest = build_s15_op_manifest(
            exhausted, started_at="2026-11-13T01:00:00+00:00", ended_at="2026-11-13T01:01:00+00:00",
            run_id="synthetic-budget-exhausted", trace_id="synthetic-budget-trace", git_sha="a" * 40,
        )
        self.assertEqual([], validate_op_run_artifact({"op_run_manifest": manifest}, is_new_run=True))
        self.assertEqual(2, manifest["max_steps"])
        self.assertEqual(2, manifest["parameters"]["query_budget"])

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
