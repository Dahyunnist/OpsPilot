"""Evaluate one frozen real-service telemetry window against lab ground truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.model_client import ChatModel, load_project_env
from opspilot.strategy_eval import STRATEGIES, evaluate_snapshot, score_case
from opspilot.telemetry import LabTelemetry
from scripts.evaluate_agents import RunBudget


TRUTH_PATH = ROOT / "lab" / "scenarios" / "truth.json"
FIXED_MODEL = "deepseek-v4-flash"
MAX_CUMULATIVE_USD = 0.50


def load_truth() -> dict[str, dict[str, Any]]:
    payload = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    return {case["case_id"]: case for case in payload["cases"]}


def _gold_evidence(roots: list[str], source_results: dict[str, dict[str, Any]]) -> set[str]:
    records = [row for result in source_results.values() for row in result.get("records", [])]
    refs: set[str] = set()
    for root in roots:
        for row in records:
            source = row.get("source")
            service = row.get("service")
            raw = row.get("raw", {})
            attrs = row.get("attributes", {})
            message = str(raw.get("message", "")).lower()
            if root == "database_pool_exhaustion":
                if (source == "logs" and service == "orders" and "database sessions remain occupied" in message) or (
                    source == "metrics" and service == "orders" and "errors_total" in str(attrs.get("name", "")) and float(attrs.get("value", 0) or 0) > 0):
                    refs.add(row["record_id"])
            elif root == "payment_dependency_timeout":
                if (source == "logs" and service == "orders" and attrs.get("error_type") == "TimeoutError") or (
                    source == "traces" and attrs.get("operation") == "payment/charge" and int(attrs.get("duration_us") or 0) >= 350_000):
                    refs.add(row["record_id"])
            elif root == "invalid_release_configuration":
                if (source == "deployments" and service == "orders" and attrs.get("version") == "v2") or (
                    source == "logs" and service == "orders" and raw.get("service_version") == "v2" and raw.get("severity") == "error") or (
                    source == "metrics" and service == "orders" and "errors_total" in str(attrs.get("name", "")) and attrs.get("version") == "v2" and float(attrs.get("value", 0) or 0) > 0):
                    refs.add(row["record_id"])
            elif root == "slow_database_query":
                if (source == "traces" and attrs.get("operation") == "postgres.query" and int(attrs.get("duration_us") or 0) >= 1_200_000) or (
                    source == "metrics" and service == "orders" and "duration_ms_sum" in str(attrs.get("name", "")) and float(attrs.get("value", 0) or 0) >= 1200):
                    refs.add(row["record_id"])
            elif root == "retry_amplification":
                if (source == "logs" and service == "orders" and "payment attempt failed" in message) or (
                    source == "traces" and service == "payment" and attrs.get("operation") == "payment/charge"):
                    refs.add(row["record_id"])
    return refs


def evaluate_case(case_id: str, start: str, end: str, strategies: tuple[str, ...],
                  budget_limit: float, cost_ledger: Path, token_budget: int) -> dict[str, Any]:
    truth_by_id = load_truth()
    if case_id not in truth_by_id:
        raise ValueError("unknown frozen lab case")
    telemetry = LabTelemetry()
    source_results = {}
    failures = {}
    for source, query in (("metrics", telemetry.metrics), ("logs", telemetry.logs),
                          ("traces", telemetry.traces), ("deployments", telemetry.deployments)):
        try:
            source_results[source] = query(start, end, 1000)
        except Exception as exc:
            failures[source] = type(exc).__name__
            source_results[source] = {"records": [], "warnings": ["source_query_failed"]}

    needs_model = any(strategy in {"single", "multi", "auto"} for strategy in strategies)
    model = None
    budget = None
    if needs_model:
        load_project_env(ROOT / ".env")
        model = ChatModel()
        if model.model != FIXED_MODEL:
            raise ValueError(f"fixed model is {FIXED_MODEL}; configured model differs")
        if budget_limit <= 0 or budget_limit > MAX_CUMULATIVE_USD:
            raise ValueError("cumulative budget cannot exceed $0.50")
        budget = RunBudget(budget_limit, cost_ledger, model.model)

    call_gate = budget.call if budget else None
    report = evaluate_snapshot(case_id, start, end, source_results, model, call_gate, strategies, token_budget)
    report = score_case(report, truth_by_id[case_id]["root_causes"], _gold_evidence(truth_by_id[case_id]["root_causes"], source_results))
    report["model"] = {"id": model.model if model else None, "temperature": 0, "token_budget_per_route": token_budget}
    report["budget"] = {"cumulative_limit_usd": budget_limit if budget else None,
        "prior_peak_estimate_usd": budget.prior_spent_usd if budget else 0.0,
        "this_run_peak_estimate_usd": round(budget.spent_usd, 8) if budget else 0.0,
        "cumulative_peak_estimate_usd": round(budget.prior_spent_usd + budget.spent_usd, 8) if budget else 0.0,
        "provider_rate_estimate_usd": {strategy: result["provider_rate_estimate_usd"] for strategy, result in report["strategies"].items()},
        "remaining_peak_reserve_usd": round(max(0.0, budget_limit - budget.prior_spent_usd - budget.spent_usd), 8) if budget else None,
        "cost_ledger": str(cost_ledger) if budget else None}
    if budget:
        budget._append({"kind": "run_summary", "run_id": budget.run_id, "benchmark": "opspilot_live_fault_lab",
            "case_id": case_id, "strategies": list(strategies), "peak_rate_estimate_usd": round(budget.spent_usd, 8),
            "provider_rate_estimate_usd": round(sum(report["budget"]["provider_rate_estimate_usd"].values()), 8),
            "cumulative_peak_estimate_usd": report["budget"]["cumulative_peak_estimate_usd"],
            "limit_usd": budget_limit, "stopped_before_overrun": any(
                "BudgetLimitExceeded" in failure for item in report["strategies"].values() for failure in item["failures"]),
        })
    report["source_failures"] = failures
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local lab strategies over one frozen telemetry window")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--start", required=True, help="ISO 8601 timestamp with timezone")
    parser.add_argument("--end", required=True, help="ISO 8601 timestamp with timezone")
    parser.add_argument("--strategies", default=",".join(STRATEGIES))
    parser.add_argument("--max-cost-usd", type=float, default=MAX_CUMULATIVE_USD)
    parser.add_argument("--cost-ledger", type=Path, default=ROOT / "reports" / "api-cost-ledger.jsonl")
    parser.add_argument("--token-budget", type=int, default=2048)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    strategies = tuple(dict.fromkeys(item.strip() for item in args.strategies.split(",") if item.strip()))
    try:
        if not strategies:
            raise ValueError("at least one strategy is required")
        if not 64 <= args.token_budget <= 8192:
            raise ValueError("token budget must be between 64 and 8192")
        if "multi" in strategies or "auto" in strategies:
            if args.token_budget < 2048:
                raise ValueError("multi-agent evaluation needs at least 2048 route tokens")
        report = evaluate_case(args.case_id, args.start, args.end, strategies, args.max_cost_usd,
            args.cost_ledger, args.token_budget)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
