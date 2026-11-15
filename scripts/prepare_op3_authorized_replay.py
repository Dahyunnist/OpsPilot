"""Prepare a hash-bound OP-3 paid-replay authorization plan without network access."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_op2_frozen import (  # noqa: E402
    CAP_USD,
    FIXED_WORST_CASE_USD,
    MAX_PROVIDER_PREFLIGHT_AGE_SECONDS,
    STRATEGIES,
    _blocked_artifact_inventory,
    _checkpoint_inventory,
    _checkpoint_path,
    _load_frozen_cases,
    _planned_requests,
    _prior_subset_spend,
    _sha256,
    _unusable_paid_prompt_keys,
    _total_cap_covers,
    _validate_new_run_id,
)
from scripts.evaluate_agents import RunBudget  # noqa: E402


def _json_hash(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _validate_preflight_report(report: dict[str, object], now: datetime | None = None) -> tuple[str, list[str]]:
    checked_at = report.get("started_at")
    provider = report.get("provider_preflight", {})
    model = report.get("model", {})
    budget = report.get("budget", {})
    if not isinstance(provider, dict) or not isinstance(model, dict) or not isinstance(budget, dict):
        raise ValueError("provider preflight report has an invalid shape")
    model_ids = model.get("provider_model_ids", [])
    if (not isinstance(checked_at, str) or provider.get("fresh") is not True
            or provider.get("balance_available") is not True
            or "CNY" not in provider.get("balance_currencies_available", [])
            or provider.get("inference_preflight") != "GET /models succeeded"
            or model.get("configured_id") != "deepseek-v4-flash"
            or not isinstance(model_ids, list)
            or not set(model_ids).intersection({"deepseek-flash", "deepseek-v4-flash"})
            or budget.get("provider_request_attempts") != 0
            or budget.get("actual_run_peak_usd") != 0):
        raise ValueError("source report lacks fresh zero-inference provider preflight evidence")
    observed_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    age_seconds = ((now or datetime.now(timezone.utc)) - observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > MAX_PROVIDER_PREFLIGHT_AGE_SECONDS:
        raise ValueError("provider preflight report is outside the one-hour freshness window")
    return checked_at, model_ids


def prepare(preflight_report: Path, run_id: str | None = None) -> dict[str, object]:
    preflight_report = preflight_report.resolve()
    try:
        preflight_rel = preflight_report.relative_to(ROOT).as_posix()
        report = json.loads(preflight_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("provider preflight report must be readable inside the workspace") from exc

    checked_at, model_ids = _validate_preflight_report(report)
    provider = report.get("provider_preflight", {})
    model = report.get("model", {})

    frozen, cases = _load_frozen_cases()
    expected_hashes = {case["spec"]["id"]: case["snapshot_sha256"] for case in cases}
    expected_ids = [case["spec"]["id"] for case in cases]
    source_cases = report.get("frozen_dataset", {}).get("cases", [])
    source_hashes = {row.get("id"): row.get("snapshot_sha256") for row in source_cases}
    if (report.get("frozen_dataset", {}).get("spec_sha256") != frozen["spec_sha256"]
            or source_hashes != expected_hashes
            or report.get("frozen_dataset", {}).get("strategies") != list(STRATEGIES)):
        raise ValueError("preflight report does not bind the exact authorized frozen inputs")

    run_id = run_id or str(uuid.uuid4())
    ledger_path = ROOT / "reports" / "api-cost-ledger.jsonl"
    _validate_new_run_id(run_id, ledger_path)
    output = ROOT / "reports" / f"op3-multiagent-authorized-replay-{run_id}.json"
    plan_path = ROOT / "reports" / f"op3-multiagent-authorized-replay-plan-{run_id}.json"
    if output.exists() or _checkpoint_path(output).exists() or plan_path.exists():
        raise FileExistsError("authorized replay output, checkpoint, or plan already exists")

    request_plan = _planned_requests(cases, STRATEGIES)
    if request_plan["max_provider_requests"] != 18:
        raise ValueError("the fixed authorized request ceiling no longer matches the frozen plan")
    reserve = Decimal(str(FIXED_WORST_CASE_USD))
    prior_peak = Decimal(str(RunBudget(CAP_USD, ledger_path, "deepseek-v4-flash").prior_spent_usd))
    prior_subset_spend = _prior_subset_spend(ledger_path)
    if not _total_cap_covers(float(prior_peak), reserve, Decimal(str(CAP_USD))):
        raise ValueError("the current ledger cannot cover the complete authorized reserve")
    unusable_keys = _unusable_paid_prompt_keys(output)
    if not unusable_keys or len(unusable_keys) > request_plan["max_provider_requests"]:
        raise ValueError("unusable prior paid prompt inventory exceeds the exact run authorization")

    payload_review_path = ROOT / "reports" / "op3-multiagent-payload-review-2026-11-15.json"
    payload_review = json.loads(payload_review_path.read_text(encoding="utf-8"))
    if (payload_review.get("status") != "verified_frozen_inputs_and_prompt_contract"
            or payload_review.get("spec_sha256") != frozen["spec_sha256"]
            or payload_review.get("request_plan_max_provider_requests") != 18
            or payload_review.get("worst_case_reserve_usd") != float(reserve)
            or payload_review.get("validation", {}).get("gold_in_payload") is not False
            or payload_review.get("validation", {}).get("credentials_in_payload") is not False
            or payload_review.get("validation", {}).get("real_customer_data_in_payload") is not False):
        raise ValueError("payload review no longer matches the authorized request boundary")

    relative = lambda path: path.resolve().relative_to(ROOT).as_posix()
    plan: dict[str, object] = {
        "plan_schema_version": 2,
        "artifact_type": "authorized_paid_replay_plan_v2",
        "status": "authorized_for_execution",
        "plan_id": str(uuid.uuid4()),
        "plan_path": relative(plan_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "planned_output": relative(output),
        "planned_checkpoint": relative(_checkpoint_path(output)),
        "scope": {
            "spec": relative(ROOT / "reports" / "op2-model-subset.json"),
            "spec_sha256": frozen["spec_sha256"],
            "case_ids": expected_ids,
            "snapshot_sha256": expected_hashes,
            "strategies": list(STRATEGIES),
            "multi_policy": "always-multi",
            "retries_enabled": False,
            "gold_sent_to_provider": False,
        },
        "authorization": {
            "user_confirmed": True,
            "source": "explicit current user instruction",
            "run_id": run_id,
            "case_ids": expected_ids,
            "snapshot_sha256": expected_hashes,
            "strategies": list(STRATEGIES),
            "max_provider_requests": 18,
            "reserve_usd": float(reserve),
            "allow_unusable_prior_paid_prompt_keys_only": _json_hash(unusable_keys),
            "gold_sent_to_provider": False,
            "secrets_sent_in_payload": False,
            "private_or_real_customer_data_sent": False,
        },
        "provider_preflight_evidence": {
            "source": preflight_rel,
            "source_sha256": _sha256(preflight_report),
            "source_run_id": report.get("run_id"),
            "observed_at": checked_at,
            "freshness": "timestamped_recent_preflight",
            "max_age_seconds": MAX_PROVIDER_PREFLIGHT_AGE_SECONDS,
            "balance_available": True,
            "balance_currencies_available": sorted(provider["balance_currencies_available"]),
            "models_endpoint": "GET /models succeeded",
            "provider_model_ids": sorted(model_ids),
            "provider": "official DeepSeek API; endpoint constrained by runner",
        },
        "request_plan": request_plan,
        "prompt_review": {
            "artifact": relative(payload_review_path),
            "sha256": _sha256(payload_review_path),
            "static_request_hashes": payload_review["static_request_hashes"],
            "dynamic_adjudicator_templates": payload_review["dynamic_adjudicator_templates"],
            "dynamic_user_hash_policy": "compute and record exact hash from this run's sanitized source-isolated expert outputs immediately before each call",
        },
        "resume_policy": {
            "reuse_requires_verified_usage": True,
            "unusable_checkpoint_action": "reissue_only_if_key_is_explicitly_authorized",
            "prior_paid_response_keys_are_allowlisted": True,
            "unusable_prior_paid_prompt_keys": unusable_keys,
            "unusable_prior_paid_prompt_keys_sha256": _json_hash(unusable_keys),
        },
        "checkpoint_inventory": _checkpoint_inventory(),
        "prior_blocked_artifacts": _blocked_artifact_inventory(),
        "budget": {
            "ledger": relative(ledger_path),
            "ledger_sha256": _sha256(ledger_path),
            "latest_ledger_peak_usd": round(float(prior_peak), 8),
            "prior_op3_subset_spend_usd": float(prior_subset_spend),
            "run_reserve_usd": float(reserve),
            "cumulative_worst_case_usd": round(float(prior_peak + reserve), 8),
            "project_cap_usd": CAP_USD,
            "within_project_cap": True,
        },
        "reserve_policy": {
            "lock": "RunBudget per-request lock",
            "reserve_before_each_provider_call": True,
            "max_provider_requests": 18,
            "max_run_reserve_usd": float(reserve),
            "fail_closed_on_cap_or_key_mismatch": True,
        },
        "data_safety": {
            "synthetic_frozen_snapshots_only": True,
            "gold_sent_to_provider": False,
            "secrets_or_credentials_written": False,
            "real_customer_data_sent": False,
            "previous_reports_overwritten": False,
        },
        "code_sha256": {
            name: _sha256(ROOT / path) for name, path in {
                "runner": "scripts/evaluate_op2_frozen.py",
                "strategy_eval": "opspilot/strategy_eval.py",
                "model_client": "opspilot/model_client.py",
                "gold_scorer": "scripts/evaluate_lab.py",
                "plan_builder": "scripts/prepare_op3_authorized_replay.py",
            }.items()
        },
        "execution_gate": {
            "provider_requests_sent_during_plan_prep": 0,
            "inference_calls_sent_during_plan_prep": 0,
            "execution_is_a_separate_explicit_command": True,
        },
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_sha256 = _sha256(plan_path)
    return {"status": "plan_prepared", "run_id": run_id, "plan_path": relative(plan_path),
        "plan_sha256": plan_sha256, "planned_output": relative(output),
        "provider_preflight_observed_at": checked_at, "provider_preflight_sha256": _sha256(preflight_report),
        "case_ids": expected_ids, "snapshot_sha256": expected_hashes,
        "strategies": list(STRATEGIES), "unusable_paid_keys_authorized": len(unusable_keys),
        "max_provider_requests": 18, "run_reserve_usd": float(reserve),
        "cumulative_worst_case_usd": round(float(prior_peak + reserve), 8),
        "inference_calls": 0}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-preflight-report", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.provider_preflight_report, args.run_id), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "plan_preparation_failed", "error": type(exc).__name__,
            "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
