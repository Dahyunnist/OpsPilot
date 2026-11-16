import hashlib
import json
from pathlib import Path

from opspilot.run_manifest import validate_op_run_artifact
from scripts.write_host_verification_manifest import build_host_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_synthetic_mcp_host_report_gets_shared_manifest_without_inventing_trace_events():
    report = {
        "artifact_type": "op2_mcp_bad_release_host_verification",
        "status": "passed",
        "run_id": "synthetic-run-001",
        "git_sha": "a" * 40,
        "started_at_utc": "2026-11-16T00:00:00Z",
        "ended_at_utc": "2026-11-16T00:00:10Z",
        "synthetic_only": True,
        "request_attempts": 1,
        "cleanup": {"containers_empty": True, "volumes_deleted": False},
        "mcp_verification": {"evidence": {"trace_id": "trace-001"}},
    }
    raw = json.dumps(report, sort_keys=True).encode("utf-8")
    report_path = ROOT / "reports" / "synthetic-host-report.json"
    artifact = build_host_manifest(report, report_path=report_path, report_bytes=raw)
    manifest = artifact["op_run_manifest"]
    assert validate_op_run_artifact(artifact, is_new_run=True) == []
    assert artifact["report_sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["status"] == "completed"
    assert manifest["trace_id"] == "trace-001"
    assert "events" not in manifest
    assert manifest["parameters"]["trace_event_evidence"] == "host report has no standardized trace event array"
    assert manifest["parameters"]["cleanup_volumes_deleted"] is False


def test_synthetic_host_failure_maps_to_failed_and_keeps_source_status():
    report = {
        "artifact_type": "op4_bad_release_host_verification",
        "status": "failed",
        "run_id": "synthetic-run-002",
        "git_sha": "b" * 40,
        "synthetic_only": True,
        "request_attempts": 1,
        "cleanup": {"containers_empty": False, "volumes_deleted": False},
    }
    raw = json.dumps(report).encode("utf-8")
    artifact = build_host_manifest(report,
        report_path=ROOT / "reports" / "synthetic-host-report.json", report_bytes=raw)
    manifest = artifact["op_run_manifest"]
    assert validate_op_run_artifact(artifact, is_new_run=True) == []
    assert manifest["status"] == "failed"
    assert manifest["parameters"]["source_status"] == "failed"
    assert manifest["trace_id"] is None
    assert manifest["parameters"]["trace_id_not_recorded"] is True


def test_bad_release_host_writer_emits_schema_manifest_and_preserves_volumes():
    source = (ROOT / "lab" / "verify-bad-release.ps1").read_text(encoding="utf-8")
    writer = (ROOT / "scripts" / "write_host_verification_manifest.py").read_text(encoding="utf-8")
    assert "write_host_verification_manifest.py" in source
    assert "git_sha = $gitSha.ToLowerInvariant()" in source
    assert "refusing to overwrite an existing manifest" in writer
    assert "validate_op_run_artifact(artifact, is_new_run=True)" in writer
    assert "$result.cleanup.volumes_deleted = $false" in source
