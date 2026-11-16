"""Write a schema-validated sidecar for a synthetic host verification report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import validate_op_run_artifact  # noqa: E402
from opspilot.verification_manifest import build_verification_run_manifest  # noqa: E402

SHA = re.compile(r"^[0-9a-f]{40}$", re.I)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_host_manifest(report: dict, *, report_path: Path, report_bytes: bytes) -> dict:
    artifact_type = report.get("artifact_type")
    evaluation_ids = {
        "op2_mcp_bad_release_host_verification": "op2_mcp_host_validation",
        "op4_bad_release_host_verification": "op4_bad_release_host_validation",
    }
    if artifact_type not in evaluation_ids:
        raise ValueError("unsupported synthetic host report artifact_type")
    run_id = report.get("run_id")
    git_sha = report.get("git_sha")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("host report run_id is missing")
    if not isinstance(git_sha, str) or not SHA.fullmatch(git_sha):
        raise ValueError("host report git_sha is missing or invalid")

    truth_path = ROOT / "lab" / "scenarios" / "truth.json"
    evaluator_path = ROOT / "lab" / "verify-bad-release.ps1"
    scorer_path = ROOT / "lab" / "telemetry-evidence.psm1"
    mcp = report.get("mcp_verification")
    evidence = mcp.get("evidence") if isinstance(mcp, dict) else None
    trace_id = evidence.get("trace_id") if isinstance(evidence, dict) else None
    if not isinstance(trace_id, str) or not trace_id.strip():
        trace_id = None

    manifest = build_verification_run_manifest(
        run_id=run_id, evaluation_id=evaluation_ids[artifact_type],
        dataset_id="opspilot_bad_release_synthetic_lab_v1",
        dataset_sha256=_sha(truth_path.read_bytes()),
        result_fingerprint_sha256=_sha(report_bytes), git_sha=git_sha,
        split="synthetic_host_validation", source_status=str(report.get("status", "")),
        evaluator_sha256=_sha(evaluator_path.read_bytes()),
        scorer_sha=_sha(scorer_path.read_bytes()),
        started_at=report.get("started_at_utc"), ended_at=report.get("ended_at_utc"),
        trace_id=trace_id,
        parameters={
            "artifact_type": "verification_report",
            "host_artifact_type": artifact_type,
            "source_report_path": report_path.relative_to(ROOT).as_posix(),
            "synthetic_only": report.get("synthetic_only") is True,
            "model_calls": 0,
            "request_attempts": report.get("request_attempts"),
            "cleanup_containers_empty": (report.get("cleanup") or {}).get("containers_empty"),
            "cleanup_volumes_deleted": (report.get("cleanup") or {}).get("volumes_deleted"),
            "trace_event_evidence": "host report has no standardized trace event array",
        },
    )
    artifact = {
        "report_path": report_path.relative_to(ROOT).as_posix(),
        "report_sha256": _sha(report_bytes),
        "op_run_manifest": manifest,
    }
    errors = validate_op_run_artifact(artifact, is_new_run=True)
    if errors:
        raise ValueError("invalid host verification manifest: " + "; ".join(errors))
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", required=True, type=Path)
    args = parser.parse_args()
    report_path = (ROOT / args.report_path).resolve()
    reports_root = (ROOT / "reports").resolve()
    try:
        report_path.relative_to(reports_root)
    except ValueError:
        parser.error("report path must stay under reports/")
    if not report_path.is_file():
        parser.error("host report does not exist")
    output_path = report_path.with_suffix(".run-manifest.json")
    if output_path.exists():
        parser.error("refusing to overwrite an existing manifest")

    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8-sig"))
    if not isinstance(report, dict):
        parser.error("host report must be a JSON object")
    artifact = build_host_manifest(report, report_path=report_path, report_bytes=report_bytes)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output_path.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
