"""Evaluate the learned routing policy on untouched validation and blind-test splits."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.agents import investigate_multi_agent, investigate_single_agent  # noqa: E402
from opspilot.cli import load_incidents  # noqa: E402
from opspilot.investigator import investigate  # noqa: E402
from opspilot.model_client import load_project_env  # noqa: E402
from opspilot.policy_learning import ContextualBandit, context_state  # noqa: E402
from opspilot.rewards import trajectory_reward  # noqa: E402
from scripts.evaluate_agents import _extract, active_deepseek_rates, run_rules, summarize  # noqa: E402


def load_policy(path: Path) -> ContextualBandit:
    trained = json.loads(path.read_text(encoding="utf-8"))
    policy = ContextualBandit(epsilon=trained["policy"]["epsilon"])
    policy.values = trained["policy"]["values"]
    policy.counts = trained["policy"]["counts"]
    return policy


def _run(route: str, incident: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if route == "rules":
        return run_rules(incident, baseline)
    if route == "single-agent":
        return investigate_single_agent(incident, baseline, token_budget=1200)
    return investigate_multi_agent(incident, baseline, token_budget=12000)


def evaluate_split(policy: ContextualBandit, split: str, reuse_test_outputs: bool = False) -> dict[str, Any]:
    data_dir = ROOT / "data" / "benchmark"
    incidents = load_incidents(data_dir / f"{split}.jsonl")
    labels = json.loads((data_dir / "labels" / f"{split}.json").read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    available_ids = {incident_id: sorted({row["record_id"] for rows in incident["signals"].values() for row in rows}) for incident_id, incident in incidents.items()}
    reference = None
    if reuse_test_outputs:
        raw = json.loads((ROOT / "reports" / "agent-evaluation.json").read_text(encoding="utf-8"))["cases"]
        reference = {route: {item["incident_id"]: item for item in items} for route, items in raw.items()}

    episodes = []
    for incident_id, incident in sorted(incidents.items()):
        baseline = investigate(incident)
        state = context_state(baseline)
        route = policy.greedy(state)
        failure = None
        if reference is not None:
            selected = reference[route][incident_id]
            prediction = selected.get("prediction", {})
            trace = selected.get("trace", {})
            model = selected.get("model", {})
            success = selected.get("success", False)
        else:
            try:
                result = _run(route, incident, baseline)
                prediction = _extract(result["adjudication"])
                trace = result.get("trace", {})
                model = result.get("model", {})
                success = True
            except Exception as exc:
                prediction = {"root_cause": None, "evidence_ids": [], "action_suggestion": None, "approval_required": False}
                trace = {"duration_ms": 0, "spent_tokens": 0, "failures": 1}
                model = {"name": os.getenv("LLM_MODEL"), "calls": 0}
                success, failure = False, type(exc).__name__
        reward = trajectory_reward(label_by_id[incident_id], prediction, trace, model, success)
        episodes.append({"incident_id": incident_id, "context": state, "route": route, "prediction": prediction,
                         "success": success, "failure_type": failure, "trace": trace, "model": model, "reward": reward})

    rates = active_deepseek_rates() if os.getenv("LLM_MODEL") and "deepseek" in os.getenv("LLM_MODEL", "").lower() else None
    summary_cases = [{"truth": label_by_id[item["incident_id"]], "prediction": item["prediction"], "success": item["success"],
                      "duration_ms": item["trace"].get("duration_ms", 0), "trace": item["trace"], "model": item["model"],
                      "available_evidence_ids": available_ids[item["incident_id"]]} for item in episodes]
    return {"split": split, "episodes": len(episodes), "policy_updates": 0,
            "route_counts": {route: sum(item["route"] == route for item in episodes) for route in ("rules", "single-agent", "multi-agent")},
            "mean_reward": round(sum(item["reward"]["total"] for item in episodes) / len(episodes), 5) if episodes else None,
            "summary": summarize(summary_cases, rates), "episodes_log": episodes}


def evaluate(policy_path: Path = ROOT / "reports" / "reward-training.json") -> dict[str, Any]:
    load_project_env()
    trained = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data" / "benchmark" / "manifest.json").read_text(encoding="utf-8"))
    policy = load_policy(policy_path)
    validation = evaluate_split(policy, "validation")
    test = evaluate_split(policy, "test", reuse_test_outputs=True)
    references = json.loads((ROOT / "reports" / "agent-evaluation.json").read_text(encoding="utf-8"))["summaries"]
    return {
        "experiment": "heldout_contextual_bandit_routing_evaluation_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy_algorithm": trained["algorithm"], "policy_fitted_split": "train",
        "policy_updated_on_validation_or_test": False, "learned_policy": trained["policy"],
        "split_manifests": {name: manifest["splits"][name] for name in ("train", "validation", "test")},
        "train": {key: trained[key] for key in ("episodes", "route_counts", "mean_reward", "estimated_cost_usd")},
        "validation": validation, "test": test, "test_strategy_reference": references,
        "interpretation": "Q values update from train rewards only. Independent validation/test matched the static rules baseline; report this as reward and routing-policy evaluation, not an Agentic-RL capability.",
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = ["# OpsPilot 路由策略奖励实验", "",
             f"训练 episodes：{result['train']['episodes']}；验证：{result['validation']['episodes']}；独立测试：{result['test']['episodes']}。", "",
             "| split | 学到的路线 | 平均 reward | 根因准确率 | 弃答召回 | tokens | 估算费用 USD |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for name in ("validation", "test"):
        row = result[name]
        summary = row["summary"]
        accuracy, abstention = summary["root_cause_accuracy"], summary["insufficient_evidence_recall"]
        lines.append(f"| {name} | {row['route_counts']} | {row['mean_reward']:.4f} | {accuracy['correct']}/{accuracy['total_decidable']} | {abstention['correct_abstentions']}/{abstention['total_insufficient']} | {summary['token_usage']['total']} | {summary['estimated_cost_usd']} |")
    lines.extend(["", "策略只在 train 上更新。Validation 与 test 上更新数均为 0。Test 复用 S8 冻结输出，避免二次模型调用。", "", result["interpretation"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=ROOT / "reports" / "reward-training.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "reward-evaluation.json")
    args = parser.parse_args()
    result = evaluate(args.policy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps({"train": result["train"], "validation_routes": result["validation"]["route_counts"], "test_routes": result["test"]["route_counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
