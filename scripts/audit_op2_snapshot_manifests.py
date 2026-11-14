"""Create local shared run-manifest records for frozen OP-2 evidence snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import (build_op_run_manifest, current_git_sha,
    validate_op_run_artifact)  # noqa: E402


SUBSET_PATH = ROOT / "reports" / "op2-model-subset.json"
TRUTH_PATH = ROOT / "lab" / "scenarios" / "truth.json"
SOURCE_NAMES = {"metrics", "logs", "traces", "deployments"}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_sha256(value: object) -> str:
    return _sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def build_audit_report(*, started_at: str | None = None, ended_at: str | None = None,
                       git_sha: str | None = None) -> dict:
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    ended_at = ended_at or started_at
    revision = git_sha or current_git_sha(ROOT)
    spec_bytes = SUBSET_PATH.read_bytes()
    spec = json.loads(spec_bytes)
    evaluator_sha = _sha256(Path(__file__).read_bytes())
    scorer_sha = _sha256((ROOT / "opspilot" / "telemetry.py").read_bytes())
    entries = []
    for item in spec["cases"]:
        relative_path = Path(item["snapshot"])
        if (relative_path.is_absolute() or ".." in relative_path.parts
                or relative_path.parts[:1] != ("reports",)):
            raise ValueError(f"snapshot path must stay under reports/: {item['snapshot']}")
        snapshot_path = ROOT / relative_path
        snapshot_bytes = snapshot_path.read_bytes()
        actual_sha = _sha256(snapshot_bytes)
        sources = json.loads(snapshot_bytes.decode("utf-8-sig"))
        source_shape_ok = isinstance(sources, dict) and set(sources) == SOURCE_NAMES
        windows = {(source.get("query", {}).get("start"), source.get("query", {}).get("end"))
            for source in sources.values() if isinstance(source, dict)} if source_shape_ok else set()
        window_consistent = source_shape_ok and len(windows) == 1 and None not in next(iter(windows), (None, None))
        hash_match = actual_sha == item["sha256"]
        status = "completed" if hash_match and window_consistent else "failed"
        summary = {"case_id": item["id"], "artifact_path": item["snapshot"],
            "expected_sha256": item["sha256"], "actual_sha256": actual_sha,
            "hash_match": hash_match, "source_shape_valid": source_shape_ok,
            "query_window_consistent": window_consistent,
            "record_counts": {name: len(sources[name].get("records", []))
                for name in sorted(SOURCE_NAMES)} if source_shape_ok else {},
            "status": status}
        manifest = build_op_run_manifest(
            project="OpsPilot", run_id=f"op2-snapshot-audit-{item['id']}-{uuid.uuid4()}",
            evaluation_id="op2_snapshot_integrity_audit", dataset_id=item["id"],
            dataset_sha256=actual_sha, truth_sha256=None, split="frozen_snapshot",
            evaluator_sha256=evaluator_sha, result_fingerprint_sha256=_json_sha256(summary),
            git_sha=revision, model_id="none", prompt_sha=None, tool_schema_sha=None,
            runtime_digest=None, max_steps=max(1, len(summary["record_counts"])), max_tokens=None,
            scorer_sha=scorer_sha, gold_source="not_applicable", cost_usd_estimate=0.0,
            trace_id=f"op2-snapshot-audit-trace-{uuid.uuid4()}", started_at=started_at,
            ended_at=ended_at, parameters={"artifact_type": "evidence_snapshot",
                "artifact_path": item["snapshot"], "manifest_expected_sha256": item["sha256"],
                "source_hash_match": hash_match, "original_capture_time_recorded": False}, status=status)
        wrapped = {"op_run_manifest": manifest}
        errors = validate_op_run_artifact(wrapped, is_new_run=True)
        if errors:
            raise ValueError("invalid OP-2 snapshot run manifest: " + "; ".join(errors))
        entries.append({**summary, "op_run_manifest": manifest})

    audit_core = {"schema_version": 1, "audit_type": "op2_evidence_snapshot_manifest_coverage",
        "started_at": started_at, "ended_at": ended_at, "dataset_manifest_path": "reports/op2-model-subset.json",
        "dataset_manifest_sha256": _sha256(spec_bytes), "original_capture_times_recorded": False,
        "external_authorization_evaluated": False, "model_calls": 0, "cost_usd_estimate": 0.0,
        "snapshots": entries}
    all_passed = all(item["status"] == "completed" for item in entries)
    audit_core["op_run_manifest"] = build_op_run_manifest(
        project="OpsPilot", run_id=f"op2-snapshot-manifest-audit-{uuid.uuid4()}",
        evaluation_id="op2_snapshot_manifest_coverage", dataset_id="op2_model_subset_v1",
        dataset_sha256=_sha256(spec_bytes), truth_sha256=None, split="registered_frozen_snapshots",
        evaluator_sha256=evaluator_sha, result_fingerprint_sha256=_json_sha256(entries),
        git_sha=revision, model_id="none", prompt_sha=None, tool_schema_sha=None,
        runtime_digest=None, max_steps=max(1, len(entries)), max_tokens=None,
        scorer_sha=scorer_sha, gold_source="not_applicable", cost_usd_estimate=0.0,
        trace_id=f"op2-snapshot-manifest-audit-trace-{uuid.uuid4()}", started_at=started_at,
        ended_at=ended_at, parameters={"artifact_type": "evidence_snapshot",
            "snapshot_count": len(entries), "original_capture_times_recorded": False},
        status="completed" if all_passed else "failed")
    errors = validate_op_run_artifact(audit_core, is_new_run=True)
    if errors:
        raise ValueError("invalid OP-2 audit run manifest: " + "; ".join(errors))
    return audit_core


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "op2-snapshot-manifest-audit-2026-11-14.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite existing audit: {args.output.name}")
    result = build_audit_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "snapshot_count": len(result["snapshots"]),
        "verified_count": sum(item["status"] == "completed" for item in result["snapshots"]),
        "model_calls": 0, "cost_usd_estimate": 0.0}, ensure_ascii=False))
    return 0 if all(item["status"] == "completed" for item in result["snapshots"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
