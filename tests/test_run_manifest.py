from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from opspilot.run_manifest import validate_run_manifest


def valid_manifest():
    return {
        "schema_version": 1, "project": "OpsPilot", "git_sha": "a" * 40,
        "dataset_id": "local-lab-v1", "dataset_sha256": "b" * 64, "split": "test",
        "model_id": "none", "prompt_sha": "c" * 64, "tool_schema_sha": "d" * 64,
        "runtime_digest": "python-3.14", "max_steps": 12, "max_tokens": 35000,
        "scorer_sha": "e" * 64, "gold_source": "independent", "cost_usd_estimate": None,
        "started_at": "2026-11-11T00:00:00Z", "ended_at": "2026-11-11T00:01:00+00:00",
        "status": "completed", "run_id": "run-s4-synthetic-v1", "trace_id": "trace-s4-synthetic-v1",
    }


class RunManifestTests(unittest.TestCase):
    def test_accepts_versioned_manifest(self):
        self.assertEqual([], validate_run_manifest(valid_manifest()))

    def test_rejects_missing_fields(self):
        manifest = valid_manifest()
        del manifest["dataset_id"]
        self.assertTrue(any("dataset_id" in error for error in validate_run_manifest(manifest)))

    def test_rejects_unknown_version(self):
        manifest = valid_manifest()
        manifest["schema_version"] = 2
        self.assertIn("unsupported schema_version", validate_run_manifest(manifest))

    def test_rejects_invalid_timestamps_and_status(self):
        manifest = valid_manifest()
        manifest.update(started_at="yesterday", status="running")
        errors = validate_run_manifest(manifest)
        self.assertTrue(any("started_at" in error for error in errors))
        self.assertIn("status is not supported", errors)

    def test_rejects_sensitive_fields(self):
        manifest = valid_manifest()
        for key in ("api_key", "access_token", "raw_prompt", "chain_of_thought"):
            candidate = valid_manifest()
            candidate[key] = "must-not-be-stored"
            self.assertTrue(any("sensitive field" in error for error in validate_run_manifest(candidate)), key)

    def test_max_tokens_budget_is_not_sensitive_data(self):
        manifest = valid_manifest()
        self.assertEqual([], validate_run_manifest(manifest))

    def test_events_require_standard_type_and_trace_context(self):
        manifest = valid_manifest()
        manifest["events"] = [{"event_type": "retry", "run_id": manifest["run_id"],
                               "trace_id": manifest["trace_id"], "span_id": "s1"}]
        self.assertEqual([], validate_run_manifest(manifest))
        for field in ("run_id", "trace_id"):
            manifest["events"][0][field] = "unrelated-" + field
            self.assertIn("event run_id and trace_id must match manifest identity", validate_run_manifest(manifest))
            manifest["events"][0][field] = manifest[field]
        manifest["events"] = [{"event_type": "mystery", "run_id": "r1"}]
        errors = validate_run_manifest(manifest)
        self.assertIn("event has unsupported event_type", errors)
        self.assertIn("event requires run_id, trace_id, and span_id", errors)

    def test_telemetry_evidence_requires_real_trace_identity_and_timing(self):
        manifest = valid_manifest()
        manifest["telemetry_evidence"] = [{
            "event_type": "span_observed", "run_id": manifest["run_id"], "trace_id": "trace-1",
            "span_id": "span-1", "service": "orders", "operation": "orders/orders",
            "order_id": "matrix-order-1", "start_unix_us": 1794553063608593,
            "duration_us": 800000,
        }]
        # Telemetry evidence uses the observed distributed trace ID, distinct from the run correlation ID.
        self.assertEqual([], validate_run_manifest(manifest))
        manifest["telemetry_evidence"][0]["run_id"] = "foreign-run"
        self.assertIn("telemetry evidence run_id must match manifest identity", validate_run_manifest(manifest))
        manifest["telemetry_evidence"][0]["run_id"] = manifest["run_id"]
        manifest["telemetry_evidence"][0]["span_id"] = ""
        errors = validate_run_manifest(manifest)
        self.assertIn("telemetry evidence requires run_id, trace_id, span_id, service, operation, and order_id", errors)

    def test_unknown_telemetry_fields_are_explicit_values(self):
        manifest = valid_manifest()
        manifest.update(prompt_sha="unknown", tool_schema_sha="unknown", runtime_digest="Engine unknown")
        self.assertEqual([], validate_run_manifest(manifest))

    def test_op1_host_manifest_matches_committed_matrix_evidence(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "reports/op1-host-matrix-run-manifest.json").read_text(encoding="utf-8"))
        report = (root / manifest["report_path"]).read_bytes()
        truth = (root / "lab/scenarios/truth.json").read_bytes()
        self.assertEqual([], validate_run_manifest(manifest))
        self.assertEqual(hashlib.sha256(report).hexdigest(), manifest["report_sha256"])
        self.assertEqual(hashlib.sha256(truth).hexdigest(), manifest["dataset_sha256"])
        self.assertEqual("967a4ecd65dd58a37680cff3cdf707c6174a3cae", manifest["git_sha"])
        self.assertEqual("unknown", manifest["docker_engine_server"])
        self.assertEqual("none", manifest["model_id"])
        self.assertEqual(48, len(manifest["telemetry_evidence"]))
        self.assertEqual(25, len(manifest["signal_evidence"]))
        self.assertTrue(all(item["run_id"] == manifest["run_id"] for item in manifest["telemetry_evidence"]))
        span_trace_ids = {item["trace_id"] for item in manifest["telemetry_evidence"]}
        self.assertTrue(span_trace_ids)
        self.assertNotIn(manifest["trace_id"], span_trace_ids)
        self.assertEqual((12, 12, 0), (manifest["case_count"], manifest["passed_count"], manifest["failed_count"]))


if __name__ == "__main__":
    unittest.main()
