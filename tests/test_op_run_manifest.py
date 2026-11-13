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
        "status": "completed",
    }


class OpRunManifestTests(unittest.TestCase):
    def test_builds_and_accepts_minimal_versioned_op_manifest(self):
        value = valid_op_manifest()
        built = build_op_run_manifest(**{key: item for key, item in value.items() if key != "schema_version"})
        self.assertEqual(value, built)
        self.assertEqual([], validate_op_run_manifest(built))
        self.assertEqual([], validate_op_run_artifact({"op_run_manifest": built}, is_new_run=True))

    def test_rejects_missing_identity_hash_and_split_fields(self):
        for field in ("run_id", "evaluation_id", "dataset_sha256", "truth_sha256", "evaluator_sha256",
                      "result_fingerprint_sha256", "split"):
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
