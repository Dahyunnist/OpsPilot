from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from opspilot.hashing import verify_text_sha256
from opspilot.run_manifest import validate_op_run_artifact


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = "reports/op3-full-offline-suite-2026-11-15.run-manifest.json"
AUDIT_PATH = ROOT / "reports" / "op3-full-offline-suite-2026-11-15.normalization-audit.json"


class Op3BlockedManifestAuditTests(unittest.TestCase):
    def test_historical_incomplete_manifest_is_preserved_as_blocked(self):
        source_path = ROOT / SOURCE_PATH
        source = json.loads(source_path.read_text(encoding="utf-8"))
        audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        manifest = source["op_run_manifest"]
        errors = validate_op_run_artifact(source, is_new_run=True)

        self.assertEqual("blocked", audit["normalization_status"])
        self.assertEqual(SOURCE_PATH, audit["source_manifest_path"])
        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), audit["source_manifest_sha256"])
        self.assertEqual(manifest["status"], audit["source_status"])
        self.assertEqual(sorted(errors), audit["validation_errors"])
        self.assertTrue(verify_text_sha256(ROOT / source["report_path"], source["report_sha256"]))
        self.assertTrue(audit["source_report_sha256_verified"])
        self.assertEqual([], audit["inferred_fields"])
        self.assertNotIn("op_run_manifest", audit)
        self.assertEqual(259, audit["recorded_parameters"]["full_suite_collected"])
        self.assertFalse(audit["recorded_parameters"]["full_suite_completed"])
        self.assertEqual(43, audit["recorded_parameters"]["focused_tests_passed"])


if __name__ == "__main__":
    unittest.main()
