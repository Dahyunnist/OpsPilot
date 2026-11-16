from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from opspilot.run_manifest import validate_op_run_artifact


ROOT = Path(__file__).resolve().parents[1]


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def test_op3_offline_record_keeps_partial_result_and_zero_model_calls():
    report = _load("reports/op3-offline-validation-2026-11-15.json")
    assert report["r3_status"] == "partial"
    assert report["r3_multi"] == "0/3"
    assert report["model_calls"] == 0
    assert report["tests"]["passed"] == 40


def test_op3_and_op4_offline_manifests_validate_and_bind_report_bytes():
    for stem in (
        "op3-offline-validation-2026-11-15",
        "op4-approval-safety-evidence-matrix-2026-11-14",
    ):
        report_path = ROOT / "reports" / f"{stem}.json"
        sidecar = _load(f"reports/{stem}.run-manifest.json")
        assert validate_op_run_artifact(sidecar, is_new_run=True) == []
        assert hashlib.sha256(report_path.read_bytes()).hexdigest() == sidecar["report_sha256"]
        assert sidecar["op_run_manifest"]["trace_id"] is None
        assert "trace_event_evidence" in sidecar["op_run_manifest"]["parameters"]


def test_op4_matrix_is_synthetic_only_and_marks_production_gaps():
    report = _load("reports/op4-approval-safety-evidence-matrix-2026-11-14.json")
    assert report["execution_scope"] == "local synthetic fixtures only"
    assert report["targeted_tests"]["passed"] == 33
    assert report["targeted_tests"]["failed"] == 0
    assert report["external_system_calls"] == 0
    assert report["production_validation"] is False
    assert any("IdP" in item for item in report["uncovered_boundaries"])


def test_later_op2_mcp_host_pass_is_scoped_to_one_synthetic_trace():
    report = _load("reports/op2-mcp-bad-release-host-5ff7d94f-01ed-4453-94b2-4551e347d2da.json")
    assert report["status"] == "passed"
    assert report["synthetic_only"] is True
    assert report["actual_http_status"] == report["expected_http_status"] == 502
    assert report["deployment"]["service"] == "orders"
    assert report["deployment"]["version"] == "v2"
    assert report["deployment"]["order_id"] == report["order_id"]
    assert report["mcp_verification"]["status"] == "passed"
    assert report["mcp_verification"]["read_calls"] == {
        "query_live_logs": 1,
        "query_live_deployments": 1,
        "query_live_traces": 1,
        "investigate_live_incident": 1,
    }
    evidence = report["mcp_verification"]["evidence"]
    assert evidence["dns_dependency_host"] == "inventory-v2"
    assert evidence["deployment_version"] == "v2"
    assert evidence["trace_id"] in {
        row["trace_id"] for row in report["matching_order_logs"] if row.get("trace_id")
    }
    assert report["cleanup"]["containers_empty"] is True
    doc = (ROOT / "docs/learning/op3-op4-offline-acceptance-2026-11-16.md").read_text(encoding="utf-8")
    assert "不证明生产 MCP" in doc


def test_release_slow_sql_report_redacts_host_paths_and_keeps_pass_evidence():
    report = _load("reports/op1-slow-sql-recheck-2b82dfd9d35349e7b4c33d76bd0c741a.json")
    serialized = json.dumps(report, ensure_ascii=False)
    windows_path = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\+(?:[^\\/:,;\"<>\s]+\\+)*[^\\/:,;\"<>\s]*")
    assert not windows_path.search(serialized)
    assert report["acceptance"]["status"] == "passed"
    assert report["fault_telemetry"]["correlated"] is True
    trace_ids = {span["trace_id"] for span in report["fault_telemetry"]["traces"]}
    assert report["fault_telemetry"]["logs"]
    assert set(report["fault_telemetry"]["trace_ids"]) <= trace_ids
    assert report["cleanup"]["containers_empty"] is True

    guide = (ROOT / "docs/learning/op4-host-validation.md").read_text(encoding="utf-8")
    assert not windows_path.search(guide)
    assert guide.count("Set-Location (git rev-parse --show-toplevel)") == 3
