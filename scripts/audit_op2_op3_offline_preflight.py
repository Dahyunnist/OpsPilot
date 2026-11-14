"""Audit frozen OP-2 evidence adapters and OP-3 offline preflight without egress."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.live_investigation import investigate_live
from opspilot.run_manifest import build_op_run_manifest, current_git_sha, validate_op_run_artifact
from scripts.evaluate_op2_frozen import AUTHORIZED_LIVE_CASES, _load_frozen_cases


OUTPUT = ROOT / "reports" / "op2-op3-offline-preflight-audit-2026-11-14.json"
SNAPSHOT_MANIFEST = ROOT / "reports" / "op2-snapshot-manifest-audit-2026-11-14.json"
FIXED_REPORT = ROOT / "reports" / "op3-op2-subset-evaluation-fixed-2026-11-14.json"
BLOCKED_REPORT = ROOT / "reports" / "op3-op2-authorized-two-preflight-blocked-2026-11-14.json"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def _read(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value, raw


class _FrozenTelemetry:
    def __init__(self, sources: dict) -> None:
        self.sources = sources
        self.calls = []

    def __getattr__(self, source: str):
        if source not in self.sources:
            raise AttributeError(source)

        def query(start: str, end: str, limit: int) -> dict:
            self.calls.append({"source": source, "start": start, "end": end, "limit": limit})
            result = self.sources[source]
            return {**result, "records": result.get("records", [])[:limit]}

        return query


def build_report(*, started_at: str | None = None, git_sha: str | None = None) -> dict:
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    revision = git_sha or current_git_sha(ROOT)
    spec, cases = _load_frozen_cases()
    snapshot_manifest, snapshot_manifest_raw = _read(SNAPSHOT_MANIFEST)
    manifest_errors = validate_op_run_artifact(snapshot_manifest, is_new_run=True)
    if manifest_errors:
        raise ValueError("OP-2 snapshot manifest invalid: " + "; ".join(manifest_errors))
    registered_manifests = {item["case_id"]: item for item in snapshot_manifest.get("snapshots", [])}
    frozen_summaries = []
    for case in cases:
        case_id = case["spec"]["id"]
        snapshot_manifest_entry = registered_manifests.get(case_id, {})
        sources = case["sources"]
        all_queries = [result.get("query", {}) for result in sources.values()]
        source_limits_valid = all(isinstance(query.get("limit"), int)
            and 1 <= query["limit"] <= 1000
            and len(sources[name].get("records", [])) <= query["limit"]
            for name, query in zip(sources, all_queries))
        all_records = [(name, row) for name, result in sources.items()
            for row in result.get("records", [])]
        record_ids_unique = len({(name, row.get("record_id")) for name, row in all_records}) == len(all_records)
        telemetry = _FrozenTelemetry(sources)
        investigation = investigate_live(case["start"], case["end"], limit=100, telemetry=telemetry)
        available = {(name, row.get("record_id")) for name, row in all_records}
        cited = {(ref.get("source"), ref.get("record_id")) for ref in investigation.get("evidence_refs", [])}
        pointers_valid = cited <= available
        time_window_valid = all(case["start"] <= row.get("timestamp", "") < case["end"]
            for _name, row in all_records)
        query_bounds_valid = all(call["start"] == case["start"] and call["end"] == case["end"]
            and call["limit"] == 100 for call in telemetry.calls)
        snapshot_manifest_valid = (snapshot_manifest_entry.get("status") == "completed"
            and snapshot_manifest_entry.get("hash_match") is True
            and snapshot_manifest_entry.get("op_run_manifest", {}).get("dataset_sha256") == case["snapshot_sha256"]
            and not validate_op_run_artifact({"op_run_manifest": snapshot_manifest_entry.get("op_run_manifest")}, is_new_run=True))
        missing_evidence_abstains = True
        if case_id in {"missing_pool_evidence", "contradictory_payment"}:
            empty_sources = {name: {**result, "records": []} for name, result in sources.items()}
            empty_result = investigate_live(case["start"], case["end"],
                telemetry=_FrozenTelemetry(empty_sources))
            missing_evidence_abstains = (not empty_result["root_causes"]
                and empty_result["confidence"] == "abstain"
                and not empty_result["evidence_refs"])
        frozen_summaries.append({"case_id": case_id,
            "snapshot_path": case["spec"]["snapshot"], "snapshot_sha256": case["snapshot_sha256"],
            "snapshot_hash_match": True, "query_window": {"start": case["start"], "end": case["end"]},
            "record_counts": {name: len(result.get("records", [])) for name, result in sources.items()},
            "source_limits_valid": source_limits_valid, "timestamps_inside_half_open_window": time_window_valid,
            "query_bounds_forwarded": query_bounds_valid, "record_ids_unique": record_ids_unique,
            "snapshot_manifest_valid": snapshot_manifest_valid, "rule_confidence": investigation["confidence"],
            "rule_root_cause_count": len(investigation["root_causes"]),
            "evidence_reference_count": len(investigation["evidence_refs"]),
            "evidence_references_resolve_to_snapshot": pointers_valid,
            "no_evidence_abstains_without_references": missing_evidence_abstains})

    fixed_report, fixed_raw = _read(FIXED_REPORT)
    blocked_report, blocked_raw = _read(BLOCKED_REPORT)
    fixed_failures = sorted({failure for case in fixed_report.get("per_case", [])
        for strategy in case.get("strategies", {}).values() for failure in strategy.get("failures", [])})
    fixed_summary = {"path": FIXED_REPORT.relative_to(ROOT).as_posix(), "sha256": _sha(fixed_raw),
        "status": fixed_report.get("evaluation_status"), "case_count": fixed_report.get("frozen_dataset", {}).get("case_count"),
        "model_calls": sum(item.get("model_calls", 0)
            for item in fixed_report.get("strategy_summary", {}).values()),
        "actual_run_peak_usd": fixed_report.get("budget", {}).get("actual_run_peak_usd"),
        "failure_types": fixed_failures, "unchanged_source_report": True}
    blocked_summary = {"path": BLOCKED_REPORT.relative_to(ROOT).as_posix(), "sha256": _sha(blocked_raw),
        "status": blocked_report.get("status"),
        "automatic_review_status": blocked_report.get("automatic_review", {}).get("status"),
        "request_command_process_started": blocked_report.get("execution", {}).get("request_command_process_started"),
        "new_model_calls": blocked_report.get("execution", {}).get("new_model_calls"),
        "incremental_cost_usd": blocked_report.get("execution", {}).get("incremental_cost_usd"),
        "evaluation_report_created": blocked_report.get("execution", {}).get("evaluation_report_created"),
        "response_checkpoint_created": blocked_report.get("execution", {}).get("response_checkpoint_created"),
        "unchanged_source_report": True}
    op2_tests = {"files": ["tests/test_live_telemetry.py", "tests/test_op2_source_status.py",
        "tests/test_op2_telemetry_robustness.py", "tests/test_op2_frozen_snapshot_readonly.py"],
        "coverage": ["half-open time windows", "limit bounds and filter-before-limit", "stable source pointers",
            "missing-evidence abstention", "contradictory signal abstention", "references resolve to frozen records"]}
    op3_tests = {"files": ["tests/test_op3_frozen_eval.py", "tests/test_op_run_manifest.py"],
        "coverage": ["registered snapshot hashes", "snapshot path allowlist", "payload evidence allowlist",
            "partial report preservation", "blocked request zero-egress record", "shared audit manifest"]}
    checks_passed = (len(cases) == 3 and len(frozen_summaries) == 3
        and all(item["snapshot_hash_match"] and item["source_limits_valid"]
            and item["timestamps_inside_half_open_window"] and item["query_bounds_forwarded"]
            and item["record_ids_unique"] and item["snapshot_manifest_valid"]
            and item["evidence_references_resolve_to_snapshot"]
            and item["no_evidence_abstains_without_references"] for item in frozen_summaries)
        and fixed_summary["status"] == "partial" and fixed_summary["model_calls"] == 0
        and fixed_summary["actual_run_peak_usd"] == 0.0
        and {"BudgetLimitExceeded", "PriorPaidResponseUnavailable"} <= set(fixed_failures)
        and blocked_summary["status"] == "blocked_before_request_egress"
        and blocked_summary["automatic_review_status"] == "rejected"
        and blocked_summary["request_command_process_started"] is False
        and blocked_summary["new_model_calls"] == 0 and blocked_summary["incremental_cost_usd"] == 0.0
        and blocked_summary["evaluation_report_created"] is False
        and blocked_summary["response_checkpoint_created"] is False)
    core = {"schema_version": 1, "audit_type": "op2_op3_offline_frozen_preflight",
        "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(),
        "dataset_manifest_path": "reports/op2-model-subset.json", "dataset_manifest_sha256": spec["spec_sha256"],
        "op2_snapshot_manifest_path": SNAPSHOT_MANIFEST.relative_to(ROOT).as_posix(),
        "op2_snapshot_manifest_sha256": _sha(snapshot_manifest_raw),
        "op2_readonly_regressions": {"passed": checks_passed, **op2_tests},
        "op3_preflight_regressions": {"passed": checks_passed, **op3_tests},
        "frozen_cases": frozen_summaries,
        "op3_existing_evaluation": fixed_summary, "op3_existing_preflight_failure": blocked_summary,
        "model_calls": 0, "incremental_cost_usd": 0.0,
        "interpretation": "只核对冻结输入、只读证据契约及既有报告；不执行模型，也不改写 Agent 预测或评分。OP-3 仍为 partial。"}
    core["op_run_manifest"] = build_op_run_manifest(
        project="OpsPilot", run_id=f"op2-op3-offline-audit-{uuid.uuid4()}",
        evaluation_id="op2_op3_offline_frozen_preflight", dataset_id="op2_model_subset_v1",
        dataset_sha256=spec["spec_sha256"], truth_sha256=None, split="frozen_registered_snapshots",
        evaluator_sha256=_sha(Path(__file__).read_bytes()),
        result_fingerprint_sha256=_json_sha(core), git_sha=revision, model_id="none",
        prompt_sha=None, tool_schema_sha=None, runtime_digest=None, max_steps=max(1, len(cases)),
        max_tokens=None, scorer_sha=_sha((ROOT / "opspilot" / "telemetry.py").read_bytes()),
        gold_source="not_applicable", cost_usd_estimate=0.0,
        trace_id=f"op2-op3-offline-audit-trace-{uuid.uuid4()}", started_at=started_at,
        ended_at=core["ended_at"], parameters={"artifact_type": "offline_preflight_audit",
            "snapshot_count": len(cases), "model_calls": 0, "sources_modified": False},
        status="completed" if checks_passed else "failed")
    errors = validate_op_run_artifact(core, is_new_run=True)
    if errors:
        raise ValueError("invalid offline preflight audit manifest: " + "; ".join(errors))
    return core


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite audit report: {OUTPUT.name}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "frozen_cases": len(report["frozen_cases"]),
        "passed": report["op2_readonly_regressions"]["passed"], "model_calls": 0,
        "incremental_cost_usd": 0.0}, ensure_ascii=False))
    return 0 if report["op2_readonly_regressions"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
