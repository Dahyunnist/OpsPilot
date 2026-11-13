"""Run the preregistered OP-2 telemetry subset through real OP-3 strategies."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.model_client import ChatModel, load_project_env  # noqa: E402
from opspilot.strategy_eval import STRATEGIES, evaluate_snapshot, score_case, summarize_cases  # noqa: E402
from scripts.evaluate_agents import RunBudget  # noqa: E402
from scripts.evaluate_lab import _gold_evidence  # noqa: E402

SUBSET_PATH = ROOT / "reports" / "op2-model-subset.json"
CAP_USD = 0.60
FIXED_WORST_CASE_USD = 0.0230226
FIXED_COMPLETION_LIMIT = 512


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_request(url: str, api_key: str) -> dict[str, Any]:
    request = Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(256_000))
    if not isinstance(payload, dict):
        raise ValueError("provider preflight returned non-object JSON")
    return payload


def _load_frozen_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec_bytes = SUBSET_PATH.read_bytes()
    spec = json.loads(spec_bytes)
    cases = []
    for item in spec["cases"]:
        path = ROOT / item["snapshot"]
        digest = _sha256(path)
        if digest != item["sha256"]:
            raise ValueError(f"frozen snapshot hash mismatch: {item['id']}")
        sources = json.loads(path.read_text(encoding="utf-8-sig"))
        if set(sources) != {"metrics", "logs", "traces", "deployments"}:
            raise ValueError(f"unexpected source set: {item['id']}")
        starts = {result.get("query", {}).get("start") for result in sources.values()}
        ends = {result.get("query", {}).get("end") for result in sources.values()}
        if len(starts) != 1 or len(ends) != 1 or None in starts or None in ends:
            raise ValueError(f"source windows differ: {item['id']}")
        cases.append({"spec": item, "snapshot_path": path, "snapshot_sha256": digest,
            "sources": sources, "start": next(iter(starts)), "end": next(iter(ends))})
    return {**spec, "spec_sha256": hashlib.sha256(spec_bytes).hexdigest()}, cases


def run(output: Path) -> dict[str, Any]:
    load_project_env(ROOT / ".env")
    configured_model = os.getenv("LLM_MODEL", "")
    base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "")
    if configured_model != "deepseek-v4-flash":
        raise ValueError("frozen OP-3 subset requires configured model deepseek-v4-flash")
    if urlsplit(base_url).scheme != "https" or urlsplit(base_url).hostname != "api.deepseek.com" or not api_key:
        raise ValueError("provider must be official DeepSeek API with configured credentials")

    frozen, cases = _load_frozen_cases()
    if frozen.get("model") != configured_model or frozen.get("max_completion_tokens") != FIXED_COMPLETION_LIMIT:
        raise ValueError("frozen subset model or completion cap differs")
    if tuple(frozen.get("strategies", [])) != STRATEGIES:
        raise ValueError("frozen subset strategy order differs")

    # Both authenticated GETs are free preflight calls. No inference request is sent here.
    balance = _json_request("https://api.deepseek.com/user/balance", api_key)
    models = _json_request("https://api.deepseek.com/models", api_key)
    if balance.get("is_available") is not True:
        raise ValueError("provider reports insufficient balance; no inference calls sent")
    if not any(float(item.get("total_balance", 0)) > 0 for item in balance.get("balance_infos", [])):
        raise ValueError("provider balance is zero; no inference calls sent")
    model_ids = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
    if not model_ids or not model_ids.intersection({"deepseek-flash", "deepseek-v4-flash"}):
        raise ValueError("provider does not list the frozen Flash model family; no inference calls sent")

    ledger = ROOT / "reports" / "api-cost-ledger.jsonl"
    budget = RunBudget(CAP_USD, ledger, configured_model)
    if budget.prior_spent_usd + FIXED_WORST_CASE_USD > CAP_USD:
        raise ValueError("frozen worst-case reservation exceeds remaining $0.60 cap; no inference calls sent")

    # Frozen registry caps completions at 512 regardless of the wider .env setting.
    os.environ["LLM_MAX_COMPLETION_TOKENS"] = str(FIXED_COMPLETION_LIMIT)
    model = ChatModel()
    started = datetime.now(timezone.utc).isoformat()
    scored_cases = []
    for case in cases:
        report = evaluate_snapshot(case["spec"]["id"], case["start"], case["end"],
            case["sources"], model, budget.call, STRATEGIES, token_budget=2048)
        truth = case["spec"]["root_causes"]
        report = score_case(report, truth, _gold_evidence(truth, case["sources"]))
        report["frozen_case"] = {"id": case["spec"]["id"], "fault": case["spec"]["fault"],
            "independent_questions": case["spec"]["independent_questions"],
            "snapshot": case["spec"]["snapshot"], "snapshot_sha256": case["snapshot_sha256"]}
        scored_cases.append(report)

    actual = round(budget.spent_usd, 8)
    cumulative = round(budget.prior_spent_usd + actual, 8)
    stopped = any("BudgetLimitExceeded" in failure for case in scored_cases
        for item in case["strategies"].values() for failure in item["failures"])
    budget._append({"kind": "run_summary", "run_id": budget.run_id,
        "benchmark": "opspilot_op2_frozen_real_agent", "strategies": list(STRATEGIES),
        "case_ids": [case["spec"]["id"] for case in cases], "peak_rate_estimate_usd": actual,
        "provider_rate_estimate_usd": round(sum(item["provider_rate_estimate_usd"]
            for case in scored_cases for item in case["strategies"].values()), 8),
        "cumulative_peak_estimate_usd": cumulative, "limit_usd": CAP_USD,
        "stopped_before_overrun": stopped})

    report = {"schema_version": 1, "benchmark": "op2_model_paired_subset",
        "run_id": budget.run_id, "started_at": started,
        "runner": "real_chat_model_and_strategy_eval", "model": {"configured_id": configured_model,
            "temperature": 0, "route_token_budget": 2048, "completion_cap": FIXED_COMPLETION_LIMIT,
            "provider_model_ids": sorted(model_ids)},
        "provider_preflight": {"balance_available": balance["is_available"],
            "balance_infos": balance.get("balance_infos", []), "inference_preflight": "GET /models succeeded"},
        "frozen_dataset": {"spec": str(SUBSET_PATH.relative_to(ROOT)),
            "spec_sha256": frozen["spec_sha256"], "case_count": len(cases),
            "strategies": list(STRATEGIES), "multi_label": "always-multi",
            "cases": [{"id": case["spec"]["id"], "snapshot": case["spec"]["snapshot"],
                "snapshot_sha256": case["snapshot_sha256"], "root_causes": case["spec"]["root_causes"],
                "gold_evidence_ids": sorted(_gold_evidence(case["spec"]["root_causes"], case["sources"]))}
                for case in cases]},
        "budget": {"cap_usd": CAP_USD, "prior_ledger_peak_usd": round(budget.prior_spent_usd, 8),
            "preregistered_worst_case_usd": FIXED_WORST_CASE_USD, "actual_run_peak_usd": actual,
            "cumulative_peak_usd": cumulative, "remaining_usd": round(CAP_USD - cumulative, 8),
            "cost_ledger": str(ledger.relative_to(ROOT))},
        "code_sha256": {name: _sha256(ROOT / path) for name, path in {
            "runner": "scripts/evaluate_op2_frozen.py", "strategy_eval": "opspilot/strategy_eval.py",
            "model_client": "opspilot/model_client.py", "gold_scorer": "scripts/evaluate_lab.py"}.items()},
        "per_case": scored_cases,
        "strategy_summary": summarize_cases(scored_cases),
        "interpretation": "真实固定快照 Agent 对照；三个合成/宿主冻结样本，不代表泛化。独立问题随案例记录，策略接收同一遥测快照。"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="send real model requests after preflight")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "op3-op2-subset-evaluation.json")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to run the preregistered real-agent comparison")
    try:
        report = run(args.output)
    except Exception as exc:
        print(json.dumps({"status": "stopped_before_or_during_run", "error": type(exc).__name__,
            "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"report": str(args.output), "run_id": report["run_id"],
        "case_count": len(report["per_case"]), "budget": report["budget"],
        "strategy_summary": report["strategy_summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
