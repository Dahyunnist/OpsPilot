"""Prepare a no-egress preregistration for the authorized OP-3 snapshot comparison."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import build_op_run_manifest, validate_op_run_artifact  # noqa: E402
from opspilot.model_client import ModelError  # noqa: E402
from opspilot.strategy_eval import STRATEGIES, failure_outcome_counts  # noqa: E402
from scripts.evaluate_agents import RunBudget  # noqa: E402
from scripts.evaluate_op2_frozen import (FIXED_WORST_CASE_USD, _load_frozen_cases,
    _planned_requests)  # noqa: E402

OUTPUT = ROOT / "reports" / "op3-multiagent-offline-prereg-2026-11-15.json"
CAP_USD = 0.60
MAX_REQUESTS = 18


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_report() -> dict[str, Any]:
    spec, cases = _load_frozen_cases()
    if tuple(spec.get("strategies", ())) != STRATEGIES:
        raise ValueError("frozen strategy order differs from evaluator")
    request_plan = _planned_requests(cases, STRATEGIES)
    if request_plan["max_provider_requests"] > MAX_REQUESTS:
        raise ValueError("planned requests exceed authorization")
    if abs(float(spec.get("total_worst_case_usd", 0)) - FIXED_WORST_CASE_USD) > 1e-12:
        raise ValueError("frozen subset reserve differs from authorization")

    ledger_path = ROOT / "reports" / "api-cost-ledger.jsonl"
    budget = RunBudget(CAP_USD, ledger_path, "deepseek-v4-flash")
    cumulative_reserve = budget.prior_spent_usd + FIXED_WORST_CASE_USD
    cases_summary = []
    for case in cases:
        sources = case["sources"]
        if set(sources) != {"metrics", "logs", "traces", "deployments"}:
            raise ValueError(f"unexpected telemetry sources: {case['spec']['id']}")
        cases_summary.append({
            "case_id": case["spec"]["id"],
            "mechanism": case["spec"]["fault"],
            "snapshot_sha256": case["snapshot_sha256"],
            "sources": sorted(sources),
            "independent_question_count": len(case["spec"]["independent_questions"]),
        })
    fault_trace_fixture = [
        ModelError("injected offline timeout", category="transport_timeout", retryable=True).diagnostic(),
        {"event": "a2a_protocol", "method": "tasks/cancel", "state": "canceled"},
    ]
    fault_metric_fixture = {
        "status": "metric_contract_test_fixture_not_experiment_result",
        "per_strategy_counts": {strategy: failure_outcome_counts(fault_trace_fixture)
            for strategy in STRATEGIES},
        "injected_trace_kinds": ["transport_timeout", "a2a tasks/cancel"],
        "model_calls": 0,
    }

    now = datetime.now(timezone.utc).isoformat()
    core: dict[str, Any] = {
        "schema_version": 1,
        "stage": "OP-3 multi-agent gain comparison",
        "status": "prepared_provider_balance_pending",
        "run_id": "op3-multiagent-offline-prereg-2026-11-15",
        "offline_only": True,
        "inference_calls": 0,
        "provider_balance_check": "not_performed",
        "provider_model_list_check": "not_performed",
        "frozen_input": {
            "spec": "reports/op2-model-subset.json",
            "spec_sha256": spec["spec_sha256"],
            "case_count": len(cases_summary),
            "synthetic": True,
            "cases": cases_summary,
        },
        "strategies": [
            {"id": strategy, "label": "always-multi" if strategy == "multi" else strategy}
            for strategy in STRATEGIES
        ],
        "request_plan": request_plan,
        "budget": {
            "project_cap_usd": CAP_USD,
            "recorded_cumulative_peak_usd": round(budget.prior_spent_usd, 8),
            "authorized_run_reserve_usd": FIXED_WORST_CASE_USD,
            "cumulative_worst_case_usd": round(cumulative_reserve, 8),
            "within_project_cap": cumulative_reserve <= CAP_USD,
            "max_provider_requests": MAX_REQUESTS,
        },
        "metric_contract": {
            "root_cause_complete_set": "exact_root_set_successes",
            "top_1": "top1_rate",
            "evidence": ["evidence_precision", "evidence_recall"],
            "provider_cost": "provider_rate_estimate_usd",
            "latency": "p95_latency_ms",
            "failures": "per-strategy failures and sanitized error_trace",
            "timeouts_and_cancellations": "failure_outcomes counted from the same sanitized trace classifier",
        },
        "offline_fault_metric_fixture": fault_metric_fixture,
        "results": None,
        "interpretation": "Input hashes, sources, strategy plan, and budget are checked offline. No model request or provider balance request was made. Gold labels are omitted from this report and must stay out of model payloads.",
        "created_at": now,
    }
    fingerprint = hashlib.sha256(json.dumps(core, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest = build_op_run_manifest(
        project="OpsPilot", run_id=core["run_id"], evaluation_id=core["run_id"],
        dataset_id="op2_model_paired_subset", dataset_sha256=spec["spec_sha256"],
        truth_sha256=None, split="authorized_frozen_three_case_subset",
        evaluator_sha256=_sha256(Path(__file__)), result_fingerprint_sha256=fingerprint,
        git_sha=__import__("subprocess").check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        model_id="none", prompt_sha=None, tool_schema_sha=None, runtime_digest=None,
        max_steps=1, max_tokens=None,
        scorer_sha=_sha256(ROOT / "opspilot" / "strategy_eval.py"),
        gold_source="not_applicable", cost_usd_estimate=0.0,
        trace_id=core["run_id"], started_at=now, ended_at=now,
        parameters={"artifact_type": "offline_preflight_audit",
            "strategies": list(STRATEGIES), "max_provider_requests": MAX_REQUESTS,
            "authorized_run_reserve_usd": FIXED_WORST_CASE_USD,
            "provider_balance_check": "not_performed"},
        status="completed")
    report = {**core, "result_fingerprint_sha256": fingerprint, "op_run_manifest": manifest}
    errors = validate_op_run_artifact(report, is_new_run=True)
    if errors:
        raise ValueError("invalid preregistration artifact: " + "; ".join(errors))
    return report


def main() -> int:
    report = prepare_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": OUTPUT.relative_to(ROOT).as_posix(),
        "status": report["status"], "cases": report["frozen_input"]["case_count"],
        "strategies": [item["id"] for item in report["strategies"]],
        "planned_requests": report["request_plan"]["max_provider_requests"],
        "cumulative_worst_case_usd": report["budget"]["cumulative_worst_case_usd"],
        "inference_calls": report["inference_calls"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
