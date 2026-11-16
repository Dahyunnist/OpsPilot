from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from opspilot.run_manifest import validate_op_run_artifact


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "op4-offline-verification-2026-11-16.json"
DATASET = "reports/op4-approval-safety-evidence-matrix-2026-11-14.json"
EVALUATOR_SOURCES = [
    "opspilot/action_safety.py",
    "opspilot/action_cli.py",
    "opspilot/identity.py",
]
ASSERTION_SOURCES = [
    "tests/test_action_safety.py",
    "tests/test_action_cli.py",
    "tests/test_s3.py",
]


def digest_paths(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(paths):
        digest.update(relative_path.encode("utf-8") + b"\0")
        digest.update((ROOT / relative_path).read_bytes() + b"\0")
    return digest.hexdigest()


class Op4OfflineVerificationManifestTests(unittest.TestCase):
    def test_fresh_offline_verification_has_complete_supported_manifest(self):
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        self.assertEqual([], validate_op_run_artifact(report, is_new_run=True))
        manifest = report["op_run_manifest"]
        self.assertEqual("completed", manifest["status"])
        self.assertIsNone(manifest["trace_id"])
        self.assertEqual(0, report["model_calls"])
        self.assertEqual(0, report["external_system_calls"])
        self.assertEqual(0, report["external_writes"])
        self.assertFalse(report["docker_started"])
        self.assertEqual(57, report["test_summary"]["passed"])
        self.assertEqual(0, report["test_summary"]["failed"])
        self.assertEqual(11, report["test_summary"]["subtests"])
        self.assertEqual(report["source_git_sha"], manifest["git_sha"])
        self.assertEqual(hashlib.sha256((ROOT / DATASET).read_bytes()).hexdigest(), manifest["dataset_sha256"])
        self.assertEqual(digest_paths(EVALUATOR_SOURCES), manifest["evaluator_sha256"])
        self.assertEqual(digest_paths(ASSERTION_SOURCES), manifest["scorer_sha"])

        result = {key: value for key, value in report.items() if key != "op_run_manifest"}
        fingerprint = hashlib.sha256(json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(fingerprint, manifest["result_fingerprint_sha256"])


if __name__ == "__main__":
    unittest.main()
