from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from opspilot.run_manifest import validate_op_run_artifact
from scripts.audit_op2_snapshot_manifests import build_audit_report
from scripts.capture_lab_snapshot import _snapshot_manifest


class Op2SnapshotManifestTests(unittest.TestCase):
    def test_existing_frozen_snapshots_get_hash_bound_audit_manifests(self):
        report = build_audit_report(git_sha="a" * 40)
        self.assertEqual([], validate_op_run_artifact(report, is_new_run=True))
        self.assertEqual(3, len(report["snapshots"]))
        self.assertTrue(all(item["hash_match"] and item["query_window_consistent"] for item in report["snapshots"]))
        self.assertTrue(all(validate_op_run_artifact({"op_run_manifest": item["op_run_manifest"]},
            is_new_run=True) == [] for item in report["snapshots"]))
        self.assertTrue(all(item["op_run_manifest"]["truth_sha256"] is None for item in report["snapshots"]))
        self.assertFalse(report["original_capture_times_recorded"])

    def test_committed_snapshot_coverage_record_has_three_valid_manifests(self):
        path = Path(__file__).resolve().parents[1] / "reports" / "op2-snapshot-manifest-audit-2026-11-14.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([], validate_op_run_artifact(report, is_new_run=True))
        self.assertEqual(3, len(report["snapshots"]))
        self.assertTrue(report["external_authorization_evaluated"] is False)
        self.assertTrue(all(item["hash_match"] and item["status"] == "completed"
            for item in report["snapshots"]))

    def test_capture_manifest_binds_raw_snapshot_and_allows_failed_capture_status(self):
        sources = {name: {"query": {"start": "2026-11-14T00:00:00Z",
            "end": "2026-11-14T00:01:00Z", "limit": 100}, "records": []}
            for name in ("metrics", "logs", "traces", "deployments")}
        started = "2026-11-14T00:00:00+00:00"
        ended = "2026-11-14T00:01:00+00:00"
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "snapshot.json"
            raw = json.dumps(sources, ensure_ascii=False, sort_keys=True).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            for status in ("completed", "failed"):
                artifact = _snapshot_manifest(output, digest, sources, started, ended, status, git_sha="b" * 40)
                self.assertEqual([], validate_op_run_artifact(artifact, is_new_run=True))
                manifest = artifact["op_run_manifest"]
                self.assertEqual(digest, manifest["result_fingerprint_sha256"])
                self.assertEqual("evidence_snapshot", manifest["parameters"]["artifact_type"])
                self.assertEqual(status, manifest["status"])


if __name__ == "__main__":
    unittest.main()
