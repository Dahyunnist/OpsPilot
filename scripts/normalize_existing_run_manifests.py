"""Write standard OP run-manifest sidecars from recorded OP-3/OP-4 evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import build_op_run_manifest, validate_op_run_artifact  # noqa: E402
from opspilot.verification_manifest import build_verification_run_manifest  # noqa: E402


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def _git_blob_sha(revision: str, relative_path: str) -> str:
    raw = subprocess.check_output(["git", "show", f"{revision}:{relative_path}"], cwd=ROOT)
    return _sha(raw)


def _write(report_path: Path, manifest: dict) -> None:
    artifact = {"report_path": report_path.relative_to(ROOT).as_posix(),
        "report_sha256": _sha(report_path.read_bytes()), "op_run_manifest": manifest}
    errors = validate_op_run_artifact(artifact, is_new_run=True)
    if errors:
        raise ValueError("invalid normalized manifest: " + "; ".join(errors))
    output = report_path.with_suffix(".run-manifest.json")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing manifest: {output.name}")
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_op3_multiagent() -> None:
    report_path = ROOT / "reports" / "op3-multiagent-fff674b6-f292-46fb-afe5-beeb5c64b563.json"
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    audit = json.loads((ROOT / "reports" / "op3-multiagent-run-audit-2026-11-15.json").read_text(encoding="utf-8-sig"))
    revision = subprocess.check_output(["git", "rev-parse", audit["execution_source_revision"]], cwd=ROOT, text=True).strip()
    manifest = build_op_run_manifest(
        project="OpsPilot", run_id=report["run_id"], evaluation_id="op3_multiagent_frozen_subset",
        dataset_id="op2_model_subset_v1", dataset_sha256=report["frozen_dataset"]["spec_sha256"],
        truth_sha256=_json_sha([{key: case[key] for key in ("id", "root_causes", "gold_evidence_ids")}
            for case in report["frozen_dataset"]["cases"]]), split="registered_frozen_snapshots",
        evaluator_sha256=report["code_sha256"]["strategy_eval"],
        result_fingerprint_sha256=_sha(report_path.read_bytes()), git_sha=revision,
        model_id=report["model"]["configured_id"], prompt_sha=None, tool_schema_sha=None,
        runtime_digest=None, max_steps=None, max_tokens=report["model"]["completion_cap"],
        scorer_sha=report["code_sha256"]["gold_scorer"], gold_source="independent",
        cost_usd_estimate=audit["execution"]["actual_cost_usd"], trace_id=None,
        started_at=report["started_at"], ended_at=None,
        parameters={"artifact_type": "model_evaluation", "multi_agent_valid_comparison": False,
            "prompt_sha_not_recorded": True,
            "tool_schema_sha_not_recorded": True, "runtime_digest_not_recorded": True,
            "max_steps_not_recorded": True, "ended_at_not_recorded": True,
            "trace_id_not_recorded": True, "trace_event_evidence": "checkpoint and response metadata only; no trace/span IDs",
            "checkpoint_path": report["request_checkpoint"], "source_audit": "reports/op3-multiagent-run-audit-2026-11-15.json"},
        status="failed")
    _write(report_path, manifest)


def normalize_op3_offline() -> None:
    report_path = ROOT / "reports" / "op3-offline-validation-2026-11-15.json"
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    revision = report["git_sha"]
    manifest = build_verification_run_manifest(
        run_id=report["run_id"], evaluation_id="op3_offline_validation",
        dataset_id="op2_model_subset_v1",
        dataset_sha256=_sha((ROOT / "reports" / "op2-model-subset.json").read_bytes()),
        split="synthetic_regression", source_status=report.get("status", "completed"),
        evaluator_sha256=_git_blob_sha(revision, "opspilot/strategy_eval.py"),
        result_fingerprint_sha256=_sha(report_path.read_bytes()), git_sha=revision,
        scorer_sha=_git_blob_sha(revision, "opspilot/strategy_eval.py"),
        started_at=None, ended_at=None, trace_id=None,
        parameters={"model_calls": 0,
            "truth_sha256_not_recorded": True, "max_steps_not_recorded": True,
            "started_at_not_recorded": True, "ended_at_not_recorded": True,
            "trace_id_not_recorded": True, "trace_event_evidence": "not emitted by offline test runner",
            "report_path": report_path.relative_to(ROOT).as_posix()})
    _write(report_path, manifest)


def normalize_op4() -> None:
    report_path = ROOT / "reports" / "op4-approval-safety-evidence-matrix-2026-11-14.json"
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    revision = report["targeted_tests"]["git_sha"]
    report_sha = _sha(report_path.read_bytes())
    source_status = "verified" if (report["targeted_tests"]["failed"] == 0
        and report["external_system_calls"] == 0
        and report["verification"]["external_writes"] == 0) else "failed"
    manifest = build_verification_run_manifest(
        run_id=report["verification"]["run_id"],
        evaluation_id="op4_approval_safety_verification",
        dataset_id="op4_approval_safety_evidence_matrix", dataset_sha256=None,
        split="synthetic_local", source_status=source_status,
        evaluator_sha256=None, result_fingerprint_sha256=report_sha, git_sha=revision,
        scorer_sha=None, started_at=None, ended_at=None, trace_id=None,
        parameters={"targeted_test_count": report["targeted_tests"]["passed"],
            "external_system_calls": report["external_system_calls"], "model_calls": report["verification"]["model_calls"],
            "trace_event_evidence": "no trace/span IDs recorded in source verification",
            "report_path": report_path.relative_to(ROOT).as_posix()})
    _write(report_path, manifest)


if __name__ == "__main__":
    normalize_op3_multiagent()
    normalize_op3_offline()
    normalize_op4()
