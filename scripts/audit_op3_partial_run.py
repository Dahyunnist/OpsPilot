"""Rebuild a privacy-filtered OP-3 summary from an existing local run report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.strategy_eval import STRATEGIES, summarize_cases  # noqa: E402


EXPECTED_CASES = {"normal_slow_sql", "missing_pool_evidence", "contradictory_payment"}
REQUEST_LIMIT = 18
PROJECT_CAP_USD = 0.60
RUN_RESERVE_USD = 0.0230226


def build_public_summary(source_path: Path) -> dict[str, Any]:
    try:
        source_bytes = source_path.read_bytes()
        report = json.loads(source_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("source evaluation report is unreadable") from exc
    if not isinstance(report, dict) or report.get("benchmark") != "op2_model_paired_subset":
        raise ValueError("source report is not an OP-3 frozen evaluation")
    cases = report.get("per_case")
    if not isinstance(cases, list) or {item.get("case_id") for item in cases if isinstance(item, dict)} != EXPECTED_CASES:
        raise ValueError("source report must contain the exact three frozen cases")
    if len(cases) != len(EXPECTED_CASES):
        raise ValueError("source report has duplicate or malformed frozen cases")
    if any(set(case.get("strategies", {})) != set(STRATEGIES) for case in cases):
        raise ValueError("source report must contain all four registered strategies per case")

    budget = report.get("budget", {})
    if not isinstance(budget, dict):
        raise ValueError("source report budget is invalid")
    attempts = budget.get("provider_request_attempts")
    max_attempts = budget.get("max_provider_requests")
    actual_cost = budget.get("actual_run_peak_usd")
    run_reserve = budget.get("preregistered_worst_case_usd")
    if (not isinstance(attempts, int) or not isinstance(max_attempts, int)
            or attempts < 0 or max_attempts != REQUEST_LIMIT or attempts > max_attempts
            or not isinstance(actual_cost, (int, float)) or actual_cost < 0
            or not isinstance(run_reserve, (int, float)) or run_reserve <= 0 or run_reserve > RUN_RESERVE_USD):
        raise ValueError("source report request count or run cost is outside the approved bound")

    auto_routes = []
    for case in cases:
        row = case["strategies"]["auto"]
        auto_routes.append({"case_id": case["case_id"], "selected_route": row.get("selected_route"),
            "fallback_route": row.get("fallback_route"), "evaluation_status": row.get("evaluation_status"),
            "failures": row.get("failures", [])})

    summary = summarize_cases(cases)
    return {
        "schema_version": 1,
        "artifact_type": "op3_partial_run_summary_audit",
        "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "run_id": report.get("run_id"),
        "evaluation_status": report.get("evaluation_status"),
        "case_count": len(cases),
        "provider_request_attempts": attempts,
        "max_provider_requests": max_attempts,
        "strategy_summary": summary,
        "latency_sample_counts": {name: {"included_completed_cases": item["completed_cases"],
            "failed_cases_without_latency": item["failed_cases"]} for name, item in summary.items()},
        "measurement_notes": {
            "cost": "usage-based run cost estimate; not an invoice",
            "latency": "p95 uses end-to-end duration_ms from completed cases only; provider-only p95 is unavailable",
            "sample_size": "three fixed synthetic cases; descriptive results only",
        },
        "auto_routes": auto_routes,
        "conclusion": "三案中 multi 的完成样本不足；全案分母计入失败后，completed-only 高分不能证明 multi 优于 single。",
        "scope": "仅离线重算原始报告的 per-case 评分；未修改原报告、ledger 或 checkpoint，未发送 provider 请求。",
        "actual_run_cost_usd": round(float(actual_cost), 8),
        "run_reserve_usd": round(float(run_reserve), 8),
        "project_cap_usd": PROJECT_CAP_USD,
        "source_artifact_status": "original report retained locally; not published",
        "scorer_sha256": hashlib.sha256((ROOT / "opspilot" / "strategy_eval.py").read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="local raw OP-3 report; read only")
    parser.add_argument("--output", type=Path, required=True, help="privacy-filtered summary path")
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    if source == output:
        parser.error("input report cannot be overwritten")
    try:
        public_summary = build_public_summary(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(public_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "summary_rebuild_failed", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr)
        return 2
    print(json.dumps({"status": "summary_rebuilt", "output": str(output),
        "source_report_sha256": public_summary["source_report_sha256"],
        "case_count": public_summary["case_count"],
        "evaluation_status": public_summary["evaluation_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
