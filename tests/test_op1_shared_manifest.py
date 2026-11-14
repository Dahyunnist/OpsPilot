from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from opspilot.run_manifest import validate_op_run_artifact
from scripts.audit_op1_host_manifests import build_coverage_report


ROOT = Path(__file__).resolve().parents[1]


class Op1SharedManifestTests(unittest.TestCase):
    def test_legacy_final_results_are_hash_bound_to_shared_audit_manifests(self):
        report = build_coverage_report(started_at="2026-11-14T00:00:00+00:00", git_sha="a" * 40)
        self.assertEqual([], validate_op_run_artifact(report, is_new_run=True))
        self.assertEqual(5, len(report["historical_reports"]))
        self.assertEqual(4, sum(item["source_matrix_status"] == "failed"
            for item in report["historical_reports"]))
        for item in report["historical_reports"]:
            self.assertEqual([], validate_op_run_artifact({"op_run_manifest": item["audit_manifest"]},
                is_new_run=True))
            self.assertEqual(item["report_sha256"], item["audit_manifest"]["result_fingerprint_sha256"])

    def test_committed_coverage_record_binds_each_existing_matrix_report(self):
        path = ROOT / "reports" / "op1-host-matrix-manifest-coverage-2026-11-14.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([], validate_op_run_artifact(report, is_new_run=True))
        self.assertEqual(5, len(report["historical_reports"]))
        self.assertTrue(all(item["report_sha256"] == item["audit_manifest"]["result_fingerprint_sha256"]
            for item in report["historical_reports"]))

    def test_preflight_failure_and_final_failure_reports_get_linked_manifests(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is required to execute the OP-1 manifest writer")
        helper = str(ROOT / "lab" / "op1-run-manifest.ps1").replace("'", "''")
        for phase, status, lab_started in (("preflight", "failed", "$false"),
                ("full_matrix", "failed", "$true"), ("full_matrix", "completed", "$true")):
            with self.subTest(phase=phase, status=status), tempfile.TemporaryDirectory() as temp:
                report = Path(temp) / "matrix.json"
                manifest_path = Path(temp) / "matrix.run-manifest.json"
                report.write_text('{"status":"' + status + '"}\n', encoding="utf-8")
                command = (
                    f". '{helper}'; Write-Op1RunManifestArtifact -ReportPath '{report}' "
                    f"-ManifestPath '{manifest_path}' -RunId 'test-run' -TraceId 'test-trace' "
                    "-StartedAt '2026-11-14T00:00:00Z' -EndedAt '2026-11-14T00:00:01Z' "
                    f"-Phase '{phase}' -Status '{status}' -CaseCount 0 -PassedCount 0 -FailedCount 0 "
                    f"-PreflightPassed {('$false' if phase == 'preflight' else '$true')} "
                    f"-LabStarted {lab_started} -CleanupEmpty $true"
                )
                subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                    check=True, capture_output=True, text=True, timeout=15)
                artifact = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                self.assertEqual([], validate_op_run_artifact(artifact, is_new_run=True))
                self.assertEqual(hashlib.sha256(report.read_bytes()).hexdigest(), artifact["report_sha256"])
                self.assertEqual(status, artifact["op_run_manifest"]["status"])
                self.assertEqual(phase, artifact["op_run_manifest"]["split"])


if __name__ == "__main__":
    unittest.main()
