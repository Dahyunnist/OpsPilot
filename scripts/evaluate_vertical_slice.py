"""Preflight and score one live, non-frozen OP-1 fault window."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.model_client import ChatModel, load_project_env
from opspilot.strategy_eval import STRATEGIES, evaluate_snapshot, score_case
from opspilot.telemetry import LabTelemetry
from scripts.evaluate_agents import BudgetLimitExceeded, RunBudget

DEFAULT_CASE = ROOT / "lab" / "scenarios" / "vertical-slow-sql.json"
DEFAULT_WINDOW = ROOT / "reports" / "vertical-slow-sql-window.json"
DEFAULT_OUTPUT = ROOT / "reports" / "vertical-slow-sql-evaluation.json"
DEFAULT_SNAPSHOT = ROOT / "reports" / "vertical-slow-sql-snapshot.json"
MAX_CUMULATIVE_USD = 0.50
MAX_ROUTE_BUDGET = 8192


def load_case(path: Path) -> dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8-sig"))
    if case.get("schema_version") != 1 or not case.get("case_id"):
        raise ValueError("invalid vertical-slice case manifest")
    injection = case.get("injection", {})
    oracle = case.get("oracle", {})
    if injection.get("fault") != "slow_sql" or injection.get("service") != "orders":
        raise ValueError("case must use the reviewed orders slow_sql injection")
    if oracle.get("root_causes") != ["slow_database_query"]:
        raise ValueError("unsupported independent oracle root cause")
    return case


def independent_gold(case: dict[str, Any], source_results: dict[str, dict[str, Any]]) -> set[str]:
    """Score only after evaluation from objective trace/metric predicates."""
    roots = case["oracle"]["root_causes"]
    gold: set[str] = set()
    for row in source_results.get("traces", {}).get("records", []):
        attrs = row.get("attributes", {})
        if "slow_database_query" in roots and attrs.get("operation") == "postgres.query" \
                and int(attrs.get("duration_us") or 0) >= 1_200_000:
            gold.add(row["record_id"])
    for row in source_results.get("metrics", {}).get("records", []):
        attrs = row.get("attributes", {})
        if "slow_database_query" in roots and row.get("service") == "orders" \
                and "duration_ms_sum" in str(attrs.get("name", "")) \
                and float(attrs.get("value") or 0) >= 1200:
            gold.add(row["record_id"])
    return gold


def collect_window(start: str, end: str, telemetry: LabTelemetry | None = None, limit: int = 1000) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    telemetry = telemetry or LabTelemetry()
    source_results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for source in ("metrics", "logs", "traces", "deployments"):
        try:
            source_results[source] = getattr(telemetry, source)(start, end, limit)
        except Exception as exc:
            failures[source] = type(exc).__name__
            source_results[source] = {"records": [], "warnings": ["source_query_failed"]}
    return source_results, failures


def _reserve(system: str, user: str, max_tokens: int, token_budget: int) -> float:
    output_cap = min(max_tokens, max(64, token_budget))
    input_reserve = int((len(system.encode("utf-8")) + len(user.encode("utf-8"))) * 1.2) + 256
    return (input_reserve * 0.30 + output_cap * 1.20) / 1_000_000


def preflight(case_id: str, start: str, end: str, source_results: dict[str, dict[str, Any]],
              max_tokens: int, token_budget: int = 2048) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    class EstimateModel:
        def __init__(self, max_tokens: int) -> None:
            self.max_tokens = max_tokens

    def no_api_gate(_operation: Any, _instance: Any, system: str, user: str, budget: int) -> dict[str, Any]:
        route = "expert" if system.startswith("You are an independent ") else (
            "adjudicator" if system.startswith("You are an adversarial ") else "single")
        requests.append({"route": route, "system": system, "user": user, "token_budget": budget})
        data = {"hypotheses": [], "missing_data": []} if route == "expert" else {
            "root_causes": [], "evidence_by_root": {}, "uncertainty": "offline cost preflight"}
        return {"data": data, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    evaluate_snapshot(case_id, start, end, source_results, EstimateModel(max_tokens), no_api_gate,
        strategies=("rules", "single", "multi", "auto"), token_budget=token_budget)
    planned: list[dict[str, Any]] = []
    for row in requests:
        planned.append({"route": row["route"], "attempt": "initial",
            "reserve_usd": _reserve(row["system"], row["user"], max_tokens, row["token_budget"])})
        if row["route"] == "expert" and row["token_budget"] < 512:
            retry_budget = min(512, max(row["token_budget"] + 64, row["token_budget"] * 2), max_tokens)
            if retry_budget > row["token_budget"]:
                planned.append({"route": row["route"], "attempt": "one_truncation_retry",
                    "reserve_usd": _reserve(row["system"], row["user"], max_tokens, retry_budget)})
    return planned


def evaluate(case_path: Path, window_path: Path, output_path: Path, snapshot_path: Path,
             *, execute: bool, max_total_usd: float, max_completion_tokens: int | None,
             cost_ledger: Path, telemetry_limit: int = 1000) -> dict[str, Any]:
    case = load_case(case_path)
    window = json.loads(window_path.read_text(encoding="utf-8-sig"))
    if window.get("case_id") != case["case_id"] or window.get("scenario") != case["injection"]["fault"]:
        raise ValueError("window does not match the selected case manifest")
    timeline = window.get("timeline", {})
    if not timeline.get("fault_window_start") or not timeline.get("fault_window_end"):
        raise ValueError("window is missing the fault interval")
    assessment_window = timeline.get("assessment_window", {})
    assessment_start = assessment_window.get("start", timeline["fault_window_start"])
    assessment_end = assessment_window.get("end", timeline["fault_window_end"])
    if max_completion_tokens is not None:
        if not 64 <= max_completion_tokens <= 4096:
            raise ValueError("max completion tokens must be between 64 and 4096")
        os.environ["LLM_MAX_COMPLETION_TOKENS"] = str(max_completion_tokens)
    load_project_env(ROOT / ".env")
    model = ChatModel()
    if model.model != "deepseek-v4-flash":
        raise ValueError("configured model differs from fixed evaluation model")
    if not 0 < max_total_usd <= MAX_CUMULATIVE_USD:
        raise ValueError("cumulative cost cap must be between zero and $0.50")
    probe = RunBudget(max_total_usd, cost_ledger, model.model)
    source_results, source_failures = collect_window(assessment_start, assessment_end, limit=telemetry_limit)
    source_counts = {name: len(result.get("records", [])) for name, result in source_results.items()}
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(source_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    incomplete = [name for name in ("metrics", "logs", "traces")
        if source_failures.get(name) or not source_counts.get(name)]
    gold = independent_gold(case, source_results)
    plan = preflight(case["case_id"], assessment_start, assessment_end,
        source_results, model.max_tokens)
    worst_case = sum(item["reserve_usd"] for item in plan)
    base = {"case_id": case["case_id"], "scenario": case["injection"]["fault"],
        "window": timeline, "assessment_window": {"start": assessment_start, "end": assessment_end},
        "source_counts": source_counts, "source_failures": source_failures,
        "model": {"id": model.model, "max_completion_tokens": model.max_tokens},
        "preflight": {"attempts": [{**item, "reserve_usd": round(item["reserve_usd"], 8)} for item in plan],
            "planned_request_attempts": len(plan), "worst_case_usd": round(worst_case, 8),
            "prior_peak_usd": round(probe.prior_spent_usd, 8), "remaining_usd": round(max(0, max_total_usd - probe.prior_spent_usd), 8),
            "cumulative_cap_usd": max_total_usd, "all_requests_pre_reserved": True},
        "independent_oracle": {"root_causes": case["oracle"]["root_causes"],
            "affected_services": case["oracle"]["affected_services"],
            "independent_questions": case["oracle"]["independent_questions"],
            "gold_evidence_ids": sorted(gold), "gold_evidence_rules": case["oracle"]["gold_evidence_rules"]},
        "snapshot_file": str(snapshot_path)}
    if incomplete or not gold:
        base["status"] = "blocked_before_model"
        base["block_reason"] = "telemetry incomplete or independent oracle evidence missing"
    elif worst_case > max(0, max_total_usd - probe.prior_spent_usd):
        base["status"] = "blocked_before_model"
        base["block_reason"] = "worst-case request reservations exceed remaining cumulative budget"
    elif not execute:
        base["status"] = "preflight_only"
    else:
        budget = RunBudget(max_total_usd, cost_ledger, model.model)
        if budget.prior_spent_usd != probe.prior_spent_usd:
            raise RuntimeError("cost ledger changed after preflight; no model calls sent")
        attempt_lock = threading.Lock()
        attempts_started = 0
        attempt_cap = len(plan)

        def bounded_gate(operation: Any, instance: Any, system: str, user: str, token_budget: int) -> dict[str, Any]:
            nonlocal attempts_started
            with attempt_lock:
                if attempts_started >= attempt_cap:
                    raise BudgetLimitExceeded("vertical-slice preflight attempt cap reached")
                attempts_started += 1
            return budget.call(operation, instance, system, user, token_budget)

        raw = evaluate_snapshot(case["case_id"], assessment_start, assessment_end,
            source_results, model, bounded_gate, strategies=("rules", "single", "multi", "auto"), token_budget=2048)
        scored = score_case(raw, case["oracle"]["root_causes"], gold)
        actual_peak = round(budget.spent_usd, 8)
        cumulative = round(budget.prior_spent_usd + budget.spent_usd, 8)
        budget._append({"kind": "run_summary", "run_id": budget.run_id,
            "benchmark": "opspilot_op1_vertical_slow_sql", "case_id": case["case_id"],
            "strategies": ["rules", "single", "multi", "auto"],
            "peak_rate_estimate_usd": actual_peak,
            "provider_rate_estimate_usd": round(sum(item["provider_rate_estimate_usd"] for item in scored["strategies"].values()), 8),
            "cumulative_peak_estimate_usd": cumulative, "limit_usd": max_total_usd,
            "stopped_before_overrun": any("BudgetLimitExceeded" in failure
                for item in scored["strategies"].values() for failure in item["failures"])})
        base.update({"status": "completed", "run_id": budget.run_id,
            "budget": {"run_peak_usd": actual_peak, "cumulative_peak_usd": cumulative,
                "remaining_usd": round(max(0, max_total_usd - cumulative), 8),
                "attempts_started": attempts_started, "attempt_cap": attempt_cap},
            "evaluation": scored})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one live OP-1 vertical-slice window")
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--window-file", type=Path, default=DEFAULT_WINDOW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-output", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--cost-ledger", type=Path, default=ROOT / "reports" / "api-cost-ledger.jsonl")
    parser.add_argument("--max-total-usd", type=float, default=MAX_CUMULATIVE_USD)
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--telemetry-limit", type=int, default=1000)
    parser.add_argument("--execute", action="store_true", help="send calls only after complete worst-case preflight")
    args = parser.parse_args()
    try:
        result = evaluate(args.case_file, args.window_file, args.output, args.snapshot_output,
            execute=args.execute, max_total_usd=args.max_total_usd,
            max_completion_tokens=args.max_completion_tokens, cost_ledger=args.cost_ledger,
            telemetry_limit=args.telemetry_limit)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "case_id": result["case_id"],
        "source_counts": result["source_counts"], "planned_request_attempts": result["preflight"]["planned_request_attempts"],
        "worst_case_usd": result["preflight"]["worst_case_usd"], "prior_peak_usd": result["preflight"]["prior_peak_usd"],
        "block_reason": result.get("block_reason"), "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] in {"preflight_only", "completed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
