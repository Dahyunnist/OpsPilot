"""Bind legacy OP-1 host matrix outputs to shared, verifiable run manifests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import build_op_run_manifest, current_git_sha, validate_op_run_artifact  # noqa: E402


REPORT_PATHS = (
    "reports/op1-host-matrix.json",
    "reports/op1-host-matrix-039e015c-failed.json",
    "reports/op1-host-matrix-12a19a4f-failed.json",
    "reports/op1-host-matrix-52df8c65-failed.json",
    "reports/op1-host-matrix-e5a90baa-11of12.json",
)
TRUTH_PATH = ROOT / "lab" / "scenarios" / "truth.json"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object) -> str:
    return _sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def build_coverage_report(*, started_at: str | None = None, git_sha: str | None = None) -> dict:
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    ended_at = started_at
    revision = git_sha or current_git_sha(ROOT)
    truth_sha = _sha256(TRUTH_PATH.read_bytes())
    evaluator_sha = _sha256(Path(__file__).read_bytes())
    scorer_sha = _sha256((ROOT / "lab" / "op1-matrix-contract.ps1").read_bytes())
    entries = []
    for relative in REPORT_PATHS:
        path = ROOT / relative
        raw = path.read_bytes()
        report = json.loads(raw.decode("utf-8-sig"))
        rows = report.get("cases", [])
        passed = sum(item.get("scenario_passed") is True for item in rows)
        failed = sum(item.get("scenario_passed") is False for item in rows)
        control_passed = bool((report.get("normal_control") or {}).get("passed"))
        cleanup_empty = bool((report.get("cleanup") or {}).get("empty"))
        source_status = "completed" if control_passed and cleanup_empty and failed == 0 else "failed"
        row_starts = sorted(item.get("start") for item in rows if item.get("start"))
        row_ends = sorted(item.get("end") for item in rows if item.get("end"))
        source_meta = report.get("run_metadata", {})
        summary = {"report_path": relative, "report_sha256": _sha256(raw),
            "source_run_id": report.get("project"), "source_matrix_status": source_status,
            "case_count": len(rows), "passed_count": passed, "failed_count": failed,
            "normal_control_passed": control_passed, "cleanup_empty": cleanup_empty,
            "source_start": source_meta.get("started_utc") or (row_starts[0] if row_starts else None),
            "source_end": source_meta.get("finished_utc") or (row_ends[-1] if row_ends else None),
            "source_git_revision_recorded": bool(source_meta.get("tested_commit"))}
        manifest = build_op_run_manifest(
            project="OpsPilot", run_id=f"op1-result-audit-{uuid.uuid4()}",
            evaluation_id="op1_host_matrix_artifact_audit", dataset_id=Path(relative).stem,
            dataset_sha256=summary["report_sha256"], truth_sha256=truth_sha,
            split="retrospective_result_integrity", evaluator_sha256=evaluator_sha,
            result_fingerprint_sha256=summary["report_sha256"], git_sha=revision,
            model_id="none", prompt_sha=None, tool_schema_sha=None, runtime_digest=None,
            max_steps=max(1, len(rows) + 1), max_tokens=None, scorer_sha=scorer_sha,
            gold_source="independent", cost_usd_estimate=0.0,
            trace_id=f"op1-result-audit-trace-{uuid.uuid4()}", started_at=started_at,
            ended_at=ended_at, parameters={"artifact_type": "op1_host_matrix",
                **summary, "manifest_purpose": "retrospective_report_integrity_audit",
                "source_git_revision": source_meta.get("tested_commit")}, status="completed")
        wrapped = {"op_run_manifest": manifest, "report_path": relative,
            "report_sha256": summary["report_sha256"]}
        errors = validate_op_run_artifact(wrapped, is_new_run=True)
        if errors:
            raise ValueError(f"invalid OP-1 manifest for {relative}: " + "; ".join(errors))
        entries.append({**summary, "audit_manifest": manifest})

    report = {"schema_version": 1, "audit_type": "op1_host_matrix_shared_manifest_coverage",
        "started_at": started_at, "ended_at": ended_at,
        "historical_reports": entries,
        "preflight_failure_writer": "lab/run-host-matrix.ps1 writes a sibling .run-manifest.json before exit 1",
        "op_run_manifest": build_op_run_manifest(
            project="OpsPilot", run_id=f"op1-manifest-coverage-{uuid.uuid4()}",
            evaluation_id="op1_host_matrix_manifest_coverage", dataset_id="op1_host_matrix_report_archive",
            dataset_sha256=_digest([item["report_sha256"] for item in entries]), truth_sha256=truth_sha,
            split="historical_reports", evaluator_sha256=evaluator_sha,
            result_fingerprint_sha256=_digest(entries), git_sha=revision, model_id="none",
            prompt_sha=None, tool_schema_sha=None, runtime_digest=None, max_steps=max(1, len(entries)),
            max_tokens=None, scorer_sha=scorer_sha, gold_source="independent", cost_usd_estimate=0.0,
            trace_id=f"op1-manifest-coverage-trace-{uuid.uuid4()}", started_at=started_at,
            ended_at=ended_at, parameters={"artifact_type": "op1_host_matrix",
                "historical_report_count": len(entries), "preflight_failure_writer_tested": True},
            status="completed")}
    errors = validate_op_run_artifact(report, is_new_run=True)
    if errors:
        raise ValueError("invalid OP-1 coverage report manifest: " + "; ".join(errors))
    return report


def main() -> int:
    output = ROOT / "reports" / "op1-host-matrix-manifest-coverage-2026-11-14.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing coverage record: {output.name}")
    report = build_coverage_report()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "historical_reports": len(report["historical_reports"]),
        "failed_matrix_reports": sum(x["source_matrix_status"] == "failed" for x in report["historical_reports"]),
        "preflight_failure_writer": "verified by offline PowerShell contract test"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
