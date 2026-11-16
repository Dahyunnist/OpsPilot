from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from opspilot.run_manifest import normalize_host_manifest, validate_public_manifest
from scripts.normalize_public_host_manifest import normalizer_code_sha256


ROOT = Path(__file__).resolve().parents[1]


def normalized_host_manifest():
    manifest_path = ROOT / "reports" / "op1-host-matrix-run-manifest.json"
    source_bytes = manifest_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    report_path = ROOT / source["report_path"]
    truth_path = ROOT / source["gold_source"]
    return normalize_host_manifest(
        source,
        source_manifest_sha256=hashlib.sha256(source_bytes).hexdigest(),
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        truth_sha256=hashlib.sha256(truth_path.read_bytes()).hexdigest(),
        normalizer_sha256=normalizer_code_sha256(),
    )


class PublicRunManifestTests(unittest.TestCase):
    def test_host_manifest_normalizes_to_public_shared_v2_without_unknown_fillers(self):
        value = normalized_host_manifest()
        self.assertEqual([], validate_public_manifest(value))
        self.assertEqual(2, value["schema_version"])
        self.assertEqual("none", value["model_id"])
        self.assertEqual("lab/scenarios/truth.json", value["gold_source"])
        self.assertEqual(0.0, value["cost_usd"])
        self.assertIsNone(value["runtime"]["digest_sha256"])
        self.assertEqual("blocked", value["field_status"]["runtime.digest_sha256"])
        self.assertEqual("blocked", value["field_status"]["scorer_sha256"])
        self.assertEqual("blocked", value["field_status"]["environment"])
        self.assertIsNone(value["trace_id"])
        self.assertEqual("not_recorded", value["field_status"]["trace_id"])
        self.assertEqual("passed", value["status"])
        self.assertEqual("blocked", value["conformance_status"])
        self.assertNotIn("unknown", json.dumps(value).casefold())
        self.assertTrue(all(item["count"] == 0 and item["coverage"] == "not_covered"
                            for item in value["trace_event_coverage"].values()))
        source = json.loads((ROOT / "reports" / "op1-host-matrix-run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(48, len(source["telemetry_evidence"]))
        self.assertTrue(all(item["span_id"] and item["trace_id"] for item in source["telemetry_evidence"]))
        self.assertNotIn(source["trace_id"], {item["trace_id"] for item in source["telemetry_evidence"]})

    def test_historical_host_missing_provenance_stays_null_and_blocked(self):
        value = normalized_host_manifest()
        expected = {
            "budget.cap_usd", "environment", "evaluation_id",
            "runtime.digest_sha256", "runtime.docker_engine_version",
            "runtime.lock_sha256", "runtime.platform", "scorer_sha256",
        }
        self.assertEqual(expected, set(value["blocked_fields"]))
        self.assertTrue(all(value[field] is None for field in (
            "environment", "evaluation_id", "scorer_sha256")))
        self.assertIsNone(value["budget"]["cap_usd"])
        self.assertTrue(all(value["runtime"][field] is None for field in (
            "digest_sha256", "docker_engine_version", "lock_sha256", "platform")))
        source = json.loads((ROOT / "reports" / "op1-host-matrix-run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("unknown", source["scorer_sha"])
        self.assertEqual("unknown", source["docker_engine_server"])
        self.assertNotIn("environment", source)
        self.assertNotIn("evaluation_id", source)

    def test_rejects_unknown_and_missing_identity(self):
        value = normalized_host_manifest()
        value["runtime"]["python"] = "unknown"
        self.assertTrue(any("unknown placeholder" in error for error in validate_public_manifest(value)))
        value = normalized_host_manifest()
        del value["run_id"]
        self.assertTrue(any("run_id" in error for error in validate_public_manifest(value)))
        value = normalized_host_manifest()
        value["run_id"] = " "
        self.assertIn("run_id must be a non-empty string", validate_public_manifest(value))

    def test_rejects_time_inversion_and_sensitive_fields(self):
        value = normalized_host_manifest()
        value["started_at"], value["completed_at"] = value["completed_at"], value["started_at"]
        self.assertIn("completed_at must not precede started_at", validate_public_manifest(value))
        value = normalized_host_manifest()
        value["runtime"]["api_key"] = "synthetic-secret"
        self.assertTrue(any("sensitive field" in error for error in validate_public_manifest(value)))

    def test_null_required_hash_needs_explicit_blocked_state(self):
        value = normalized_host_manifest()
        value["scorer_sha256"] = None
        value["field_status"].pop("scorer_sha256")
        self.assertTrue(any("scorer_sha256" in error for error in validate_public_manifest(value)))

    def test_null_trace_id_requires_explicit_state(self):
        value = normalized_host_manifest()
        value["field_status"].pop("trace_id")
        self.assertIn("null trace_id requires explicit field_status", validate_public_manifest(value))

    def test_conformance_cannot_pass_with_blocked_fields(self):
        value = normalized_host_manifest()
        value["conformance_status"] = "passed"
        self.assertIn("conformance_status cannot pass while blocked_fields are present",
                      validate_public_manifest(value))

    def test_committed_public_view_matches_reproducible_normalization(self):
        expected = normalized_host_manifest()
        actual_path = ROOT / "reports" / "op1-host-matrix-public-manifest.json"
        actual = json.loads(actual_path.read_text(encoding="utf-8"))
        self.assertEqual(expected, actual)
        self.assertEqual([], validate_public_manifest(actual))


if __name__ == "__main__":
    unittest.main()
