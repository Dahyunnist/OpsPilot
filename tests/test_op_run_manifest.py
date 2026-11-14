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
    def test_builds_and_accepts_versioned_shared_op_manifest(self):
        value = valid_op_manifest()
        built = build_op_run_manifest(**{key: item for key, item in value.items() if key != "schema_version"})
        self.assertEqual(value, built)
        self.assertEqual([], validate_op_run_manifest(built))
        self.assertEqual([], validate_op_run_artifact({"op_run_manifest": built}, is_new_run=True))

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
