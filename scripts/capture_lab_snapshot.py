"""Freeze live, loopback telemetry before a fault-bearing service restarts."""

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

from opspilot.telemetry import LabTelemetry
from opspilot.run_manifest import build_op_run_manifest, current_git_sha, validate_op_run_artifact


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_manifest(output: Path, snapshot_sha256: str, sources: dict,
                       started_at: str, ended_at: str, status: str,
                       *, git_sha: str | None = None) -> dict:
    capture_windows = [result.get("query", {}) for result in sources.values() if isinstance(result, dict)]
    starts = sorted({item.get("start") for item in capture_windows if item.get("start")})
    ends = sorted({item.get("end") for item in capture_windows if item.get("end")})
    limits = sorted({item.get("limit") for item in capture_windows if item.get("limit") is not None})
    evaluator_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    telemetry_path = ROOT / "opspilot" / "telemetry.py"
    telemetry_sha = hashlib.sha256(telemetry_path.read_bytes()).hexdigest()
    manifest = build_op_run_manifest(
        project="OpsPilot", run_id=str(uuid.uuid4()), evaluation_id="op2_evidence_snapshot_capture",
        dataset_id="op2_loopback_telemetry_snapshot", dataset_sha256=snapshot_sha256,
        truth_sha256=None, split="read_only_capture", evaluator_sha256=evaluator_sha,
        result_fingerprint_sha256=snapshot_sha256, git_sha=git_sha or current_git_sha(ROOT),
        model_id="none", prompt_sha=None, tool_schema_sha=None, runtime_digest=None,
        max_steps=4, max_tokens=None, scorer_sha=telemetry_sha, gold_source="not_applicable",
        cost_usd_estimate=0.0, trace_id=str(uuid.uuid4()), started_at=started_at,
        ended_at=ended_at, parameters={"artifact_type": "evidence_snapshot", "artifact_path": str(output),
            "source_names": sorted(sources), "record_counts": {name: len(item.get("records", []))
                for name, item in sources.items() if isinstance(item, dict)},
            "query_starts": starts, "query_ends": ends, "query_limits": limits,
            "model_calls": 0}, status=status)
    artifact = {"op_run_manifest": manifest, "artifact_path": str(output),
        "artifact_sha256": snapshot_sha256}
    errors = validate_op_run_artifact(artifact, is_new_run=True)
    if errors:
        raise ValueError("refusing to write unmanifested OP-2 snapshot: " + "; ".join(errors))
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    started_at = _utc_now()
    telemetry = LabTelemetry()
    sources = {}
    for name in ("metrics", "logs", "traces", "deployments"):
        sources[name] = getattr(telemetry, name)(args.start, args.end, args.limit)
    ended_at = _utc_now()
    missing = [name for name in ("metrics", "logs", "traces") if not sources[name].get("records")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = sources if not missing else {"capture_status": "failed", "missing_sources": missing, "sources": sources}
    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.output.write_bytes(snapshot_bytes)
    snapshot_sha = hashlib.sha256(snapshot_bytes).hexdigest()
    manifest_artifact = _snapshot_manifest(args.output, snapshot_sha, sources, started_at, ended_at,
        "failed" if missing else "completed")
    manifest_path = args.manifest_output or args.output.with_suffix(".run-manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest_artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "manifest": str(manifest_path),
        "status": "failed" if missing else "completed",
        "counts": {key: len(value.get("records", [])) for key, value in sources.items()}}, ensure_ascii=False))
    return 2 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
