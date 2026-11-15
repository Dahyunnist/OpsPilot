"""Record an offline OP-3 stop after the read-only provider balance preflight failed."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import build_op_run_manifest, validate_op_run_artifact  # noqa: E402
from opspilot.strategy_eval import STRATEGIES  # noqa: E402
from scripts.evaluate_agents import RunBudget  # noqa: E402
from scripts.evaluate_op2_frozen import (CAP_USD, FIXED_WORST_CASE_USD,
    _load_frozen_cases, _planned_requests)  # noqa: E402

RUN_ID = "fff674b6-f292-46fb-afe5-beeb5c64b563"
OUTPUT = ROOT / "reports" / "op3-multiagent-provider-preflight-2026-11-15.json"
MANIFEST_OUTPUT = OUTPUT.with_suffix(".run-manifest.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report() -> dict[str, Any]:
    spec, cases = _load_frozen_cases()
    plan = _planned_requests(cases, STRATEGIES)
    if plan["max_provider_requests"] != 18:
        raise ValueError("authorized request plan changed")
    ledger_path = ROOT / "reports" / "api-cost-ledger.jsonl"
    budget = RunBudget(CAP_USD, ledger_path, "deepseek-v4-flash")
    reserve = Decimal(str(FIXED_WORST_CASE_USD))
    cumulative_peak = Decimal(str(budget.prior_spent_usd)) + reserve
    if cumulative_peak > Decimal(str(CAP_USD)):
        raise ValueError("project cost cap cannot cover the authorized reserve")

    now = datetime.now(timezone.utc).isoformat()
    core: dict[str, Any] = {
        "schema_version": 1,
        "stage": "OP-3 multi-agent gain comparison",
        "run_id": RUN_ID,
        "status": "blocked_read_only_balance_preflight",
        "created_at": now,
        "offline_artifact": True,
        "frozen_spec_sha256": spec["spec_sha256"],
        "authorized_snapshots": {case["spec"]["id"]: case["snapshot_sha256"] for case in cases},
        "strategies": ["rules", "single", "always-multi", "auto"],
        "max_provider_requests": plan["max_provider_requests"],
        "request_plan": plan,
        "budget": {
            "project_cap_usd": float(CAP_USD),
            "recorded_cumulative_peak_usd": float(budget.prior_spent_usd),
            "authorized_run_reserve_usd": float(reserve),
            "cumulative_worst_case_usd": float(cumulative_peak),
            "remaining_after_worst_case_usd": float(Decimal(str(CAP_USD)) - cumulative_peak),
            "project_cap_passed": True,
        },
        "provider_preflight": {
            "balance_endpoint": "https://api.deepseek.com/user/balance",
            "status": "blocked_before_response",
            "error_type": "URLError",
            "error_code": "WinError 10013",
            "balance": "unknown",
            "model_list_check": "not_reached",
        },
        "execution": {
            "inference_started": False,
            "model_calls": 0,
            "new_cost_usd": 0.0,
            "evaluation_report_written": False,
            "request_checkpoint_written": False,
            "gold_in_payload": False,
            "external_writes": 0,
        },
        "comparison_results": None,
        "interpretation": "Snapshot hashes, four strategies, request ceiling, and project reserve passed locally. Provider balance could not be read, so no model request or comparison result exists.",
    }
    fingerprint = hashlib.sha256(json.dumps(core, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest = build_op_run_manifest(
        project="OpsPilot", run_id=RUN_ID, evaluation_id=RUN_ID,
        dataset_id="op2_model_paired_subset", dataset_sha256=spec["spec_sha256"],
        truth_sha256=None, split="authorized_frozen_three_case_subset",
        evaluator_sha256=_sha256(Path(__file__)), result_fingerprint_sha256=fingerprint,
        git_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        model_id="none", prompt_sha=None, tool_schema_sha=None, runtime_digest=None,
        max_steps=1, max_tokens=None, scorer_sha=_sha256(ROOT / "opspilot" / "strategy_eval.py"),
        gold_source="not_applicable", cost_usd_estimate=0.0, trace_id=RUN_ID,
        started_at=now, ended_at=now,
        parameters={"artifact_type": "offline_preflight_audit",
            "provider_balance_check": "blocked_before_response",
            "max_provider_requests": 18,
            "authorized_run_reserve_usd": FIXED_WORST_CASE_USD},
        status="failed")
    report = {**core, "result_fingerprint_sha256": fingerprint, "op_run_manifest": manifest}
    errors = validate_op_run_artifact(report, is_new_run=True)
    if errors:
        raise ValueError("invalid blocked preflight artifact: " + "; ".join(errors))
    return report


def main() -> int:
    if OUTPUT.exists() or MANIFEST_OUTPUT.exists():
        raise FileExistsError("refusing to overwrite an existing preflight artifact")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sidecar = {
        "report_path": OUTPUT.relative_to(ROOT).as_posix(),
        "report_sha256": _sha256(OUTPUT),
        "op_run_manifest": report["op_run_manifest"],
    }
    MANIFEST_OUTPUT.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": OUTPUT.relative_to(ROOT).as_posix(),
        "status": report["status"], "balance": "unknown", "model_calls": 0,
        "recorded_cumulative_peak_usd": report["budget"]["recorded_cumulative_peak_usd"],
        "cumulative_worst_case_usd": report["budget"]["cumulative_worst_case_usd"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
