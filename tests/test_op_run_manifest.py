from __future__ import annotations

import json
from pathlib import Path
import unittest

from opspilot.run_manifest import (
    build_op_run_manifest,
    validate_op_run_artifact,
    validate_op_run_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def valid_op_manifest():
    return {
        "schema_version": 1,
        "project": "OpsPilot",
        "run_id": "mock-run-2026-11-13-01",
        "evaluation_id": "opspilot_causal_chains_v1",
        "dataset_id": "opspilot_causal_chains_v1",
        "dataset_sha256": "a" * 64,
        "truth_sha256": "b" * 64,
        "split": "test",
        "evaluator_sha256": "c" * 64,
        "result_fingerprint_sha256": "d" * 64,
        "git_sha": "e" * 40,
        "model_id": "none",
        "prompt_sha": None,
        "tool_schema_sha": None,
        "runtime_digest": None,
        "max_steps": 8,
        "max_tokens": None,
        "scorer_sha": "f" * 64,
        "gold_source": "independent",
        "cost_usd_estimate": 0.0,
        "trace_id": "mock-trace-2026-11-13-01",
        "started_at": "2026-11-13T01:00:00+00:00",
        "ended_at": "2026-11-13T01:01:00+00:00",
        "parameters": {"query_budget": 8, "max_rows_per_source": 100},
        "status": "completed",
    }


class OpRunManifestTests(unittest.TestCase):
    def test_four_project_artifacts_have_minimum_manifest_fields_and_execution_provenance(self):
        minimum = {"project", "git_sha", "dataset_id", "dataset_sha256", "split", "model_id",
            "prompt_sha", "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens",
            "scorer_sha", "gold_source", "cost_usd_estimate"}
        paths = [
            ROOT / "reports" / "op1-host-matrix-reproduction-2026-11-15.run-manifest.json",
            ROOT / "reports" / "op3-multiagent-fff674b6-f292-46fb-afe5-beeb5c64b563.run-manifest.json",
            ROOT / "reports" / "op3-offline-validation-2026-11-15.run-manifest.json",
            ROOT / "reports" / "op4-approval-safety-evidence-matrix-2026-11-14.run-manifest.json",
        ]
        manifests = []
        for path in paths:
            with self.subTest(path=path.name):
                artifact = json.loads(path.read_text(encoding="utf-8"))
                manifest = artifact["op_run_manifest"]
                self.assertTrue(minimum <= manifest.keys())
                self.assertEqual([], validate_op_run_artifact(artifact, is_new_run=True))
                manifests.append(manifest)

        op1_report = json.loads((ROOT / "reports" / "op1-host-matrix-reproduction-2026-11-15.json").read_text(encoding="utf-8"))
        trace_ids = set()
        span_ids = set()
        def collect_traces(value):
            if isinstance(value, dict):
                if isinstance(value.get("trace_id"), str):
                    trace_ids.add(value["trace_id"])
                if isinstance(value.get("span_id"), str):
                    span_ids.add(value["span_id"])
                for child in value.values():
                    collect_traces(child)
            elif isinstance(value, list):
                for child in value:
                    collect_traces(child)
        collect_traces(op1_report)
        self.assertIn(manifests[0]["trace_id"], trace_ids)
        self.assertTrue(span_ids)
        self.assertEqual(op1_report["run_id"], manifests[0]["run_id"])

        op2_report = json.loads((ROOT / "reports" / "op2-snapshot-manifest-audit-2026-11-15.json").read_text(encoding="utf-8"))
        self.assertIsNone(op2_report["op_run_manifest"]["trace_id"])
        self.assertTrue(all(item["op_run_manifest"]["trace_id"] is None for item in op2_report["snapshots"]))
        self.assertTrue(all(item["op_run_manifest"]["run_id"] == op2_report["op_run_manifest"]["run_id"]
            for item in op2_report["snapshots"]))
        self.assertIsNone(json.loads((ROOT / "reports" /
            "op2-op3-offline-preflight-audit-2026-11-15.json").read_text(encoding="utf-8"))["op_run_manifest"]["trace_id"])
        historical_op2 = json.loads((ROOT / "reports" /
            "op2-snapshot-manifest-audit-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual([], validate_op_run_artifact(historical_op2, is_new_run=True))
        self.assertIsNone(historical_op2["op_run_manifest"]["trace_id"])
        self.assertTrue(all(item["op_run_manifest"]["trace_id"] is None for item in historical_op2["snapshots"]))
        historical_preflight = json.loads((ROOT / "reports" /
            "op2-op3-offline-preflight-audit-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual([], validate_op_run_artifact(historical_preflight, is_new_run=True))
        self.assertIsNone(historical_preflight["op_run_manifest"]["trace_id"])

        op3_run = json.loads((ROOT / "reports" / "op3-multiagent-fff674b6-f292-46fb-afe5-beeb5c64b563.json").read_text(encoding="utf-8"))
        self.assertEqual(op3_run["run_id"], manifests[1]["run_id"])
        self.assertIsNone(manifests[1]["trace_id"])
        self.assertIn("trace_event_evidence", manifests[1]["parameters"])

        op4_report = json.loads((ROOT / "reports" / "op4-approval-safety-evidence-matrix-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual(op4_report["verification"]["run_id"], manifests[3]["run_id"])
        self.assertIsNone(manifests[3]["trace_id"])
        self.assertIsNone(manifests[3]["dataset_sha256"])
        self.assertTrue(manifests[3]["parameters"]["dataset_sha256_not_recorded"])

    def test_builds_and_accepts_versioned_shared_op_manifest(self):
        value = valid_op_manifest()
        built = build_op_run_manifest(**{key: item for key, item in value.items() if key != "schema_version"})
        self.assertEqual(value, built)
        self.assertEqual([], validate_op_run_manifest(built))
        self.assertEqual([], validate_op_run_artifact({"op_run_manifest": built}, is_new_run=True))

    def test_unknown_trace_is_null_unless_execution_trace_was_recorded(self):
        manifest = valid_op_manifest()
        manifest["trace_id"] = None
        self.assertEqual([], validate_op_run_manifest(manifest))
        manifest["trace_id"] = " "
        self.assertTrue(any("trace_id must be null" in error for error in validate_op_run_manifest(manifest)))

    def test_unrecorded_max_steps_must_be_explicitly_marked(self):
        manifest = valid_op_manifest()
        manifest.update(max_steps=None, parameters={"max_steps_not_recorded": True})
        self.assertEqual([], validate_op_run_manifest(manifest))
        manifest["parameters"] = {}
        self.assertTrue(any("max_steps must be a positive integer" in error
                            for error in validate_op_run_manifest(manifest)))

    def test_unknown_provenance_and_times_must_be_explicitly_marked(self):
        manifest = valid_op_manifest()
        manifest.update(evaluator_sha256=None, scorer_sha=None, started_at=None, ended_at=None,
            max_tokens=None, parameters={"evaluator_sha256_not_recorded": True,
                "scorer_sha_not_recorded": True, "started_at_not_recorded": True,
                "ended_at_not_recorded": True, "max_tokens_not_recorded": True})
        self.assertEqual([], validate_op_run_manifest(manifest))
        manifest["parameters"].pop("ended_at_not_recorded")
        self.assertTrue(any(error.startswith("ended_at must be an ISO-8601")
                            for error in validate_op_run_manifest(manifest)))

    def test_rejects_missing_identity_hash_and_split_fields(self):
        for field in ("run_id", "evaluation_id", "dataset_sha256", "truth_sha256", "evaluator_sha256",
                      "result_fingerprint_sha256", "split", "git_sha", "model_id", "prompt_sha",
                      "tool_schema_sha", "runtime_digest", "max_steps", "max_tokens", "scorer_sha",
                      "gold_source", "cost_usd_estimate", "trace_id", "started_at", "ended_at", "parameters"):
            with self.subTest(field=field):
                candidate = valid_op_manifest()
                del candidate[field]
                errors = validate_op_run_manifest(candidate)
                self.assertTrue(any(field in error for error in errors))

    def test_rejects_empty_identity_split_and_bad_hashes(self):
        candidate = valid_op_manifest()
        candidate.update(run_id=" ", split="", dataset_sha256="not-a-sha256")
        errors = validate_op_run_manifest(candidate)
        self.assertTrue(any("run_id must be a non-empty string" == error for error in errors))
        self.assertTrue(any("split must be a non-empty string" == error for error in errors))
        self.assertTrue(any("dataset_sha256 must be 64 hexadecimal characters" == error for error in errors))

    def test_rejects_boolean_version_and_non_string_hash(self):
        candidate = valid_op_manifest()
        candidate["schema_version"] = True
        candidate["truth_sha256"] = 123
        errors = validate_op_run_manifest(candidate)
        self.assertIn("unsupported OP run schema_version", errors)
        self.assertIn("truth_sha256 must be 64 hexadecimal characters", errors)

    def test_rejects_unknown_model_identity_and_invalid_shared_hashes(self):
        candidate = valid_op_manifest()
        candidate.update(model_id="unknown", git_sha="bad", scorer_sha="bad")
        errors = validate_op_run_manifest(candidate)
        self.assertIn("model_id must identify the model or be none", errors)
        self.assertIn("git_sha must be 40 hexadecimal characters", errors)
        self.assertIn("scorer_sha must be 64 hexadecimal characters", errors)

    def test_no_model_contract_requires_null_not_fabricated_hashes_and_zero_cost(self):
        valid = valid_op_manifest()
        self.assertEqual([], validate_op_run_manifest(valid))
        invalid = {**valid, "prompt_sha": "a" * 64, "tool_schema_sha": "b" * 64,
                   "runtime_digest": "c" * 64,
                   "cost_usd_estimate": 0.01, "gold_source": "unknown"}
        errors = validate_op_run_manifest(invalid)
        self.assertIn("model_id none requires prompt_sha null", errors)
        self.assertIn("model_id none requires tool_schema_sha null", errors)
        self.assertIn("model_id none requires runtime_digest null", errors)
        self.assertIn("model_id none requires zero estimated cost", errors)
        self.assertIn("model_id none requires independent gold or an explicit non-evaluative artifact type", errors)

    def test_non_evaluative_artifacts_can_declare_truth_not_applicable(self):
        for artifact_type in ("evidence_snapshot", "action_dry_run", "offline_preflight_audit"):
            with self.subTest(artifact_type=artifact_type):
                manifest = valid_op_manifest()
                manifest.update(evaluation_id=f"op-{artifact_type}", truth_sha256=None,
                    gold_source="not_applicable", parameters={"artifact_type": artifact_type})
                self.assertEqual([], validate_op_run_manifest(manifest))
        invalid = valid_op_manifest()
        invalid.update(truth_sha256=None, gold_source="not_applicable", parameters={"artifact_type": "other"})
        self.assertTrue(any("truth_sha256" in error for error in validate_op_run_manifest(invalid)))

    def test_new_report_requires_manifest_but_historical_report_stays_readable(self):
        self.assertIn("new OP run requires op_run_manifest",
                      validate_op_run_artifact({"evaluation": "old"}, is_new_run=True))
        self.assertEqual([], validate_op_run_artifact({"evaluation": "old"}, is_new_run=False))
        legacy_path = ROOT / "reports" / "agent-evaluation.json"
        raw_before = legacy_path.read_bytes()
        legacy = json.loads(raw_before)
        self.assertEqual([], validate_op_run_artifact(legacy, is_new_run=False))
        self.assertEqual(raw_before, legacy_path.read_bytes())

    def test_mock_writer_validates_new_manifest_before_write(self):
        source = (ROOT / "scripts" / "evaluate_s15_mock_ablation.py").read_text(encoding="utf-8")
        self.assertIn("build_op_run_manifest(", source)
        self.assertIn("validate_op_run_artifact(report, is_new_run=True)", source)


if __name__ == "__main__":
    unittest.main()
