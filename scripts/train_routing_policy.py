"""Learn a contextual routing policy from training-split incident rewards."""

from __future__ import annotations

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
from scripts.evaluate_agents import _extract, active_deepseek_rates, estimate_cost, run_rules  # noqa: E402


def train(output: Path = ROOT / "reports" / "reward-training.json") -> dict[str, Any]:
    load_project_env()
    data_dir = ROOT / "data" / "benchmark"
    incidents = load_incidents(data_dir / "train.jsonl")
    labels = json.loads((data_dir / "labels" / "train.json").read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    policy = ContextualBandit(epsilon=0.1, seed=20261021)
    episodes = []
    for incident_id, incident in sorted(incidents.items()):
        baseline = investigate(incident, query_budget=8)
        state = context_state(baseline)
        route = policy.choose(state)
        try:
            if route == "rules":
                result = run_rules(incident, baseline)
            elif route == "single-agent":
                result = investigate_single_agent(incident, baseline, token_budget=1200)
            else:
                result = investigate_multi_agent(incident, baseline, token_budget=12000)
            prediction = _extract(result["adjudication"])
            trace, model = result.get("trace", {}), result.get("model", {})
            success, failure = True, None
        except Exception as exc:
            prediction = {"root_cause": None, "evidence_ids": [], "action_suggestion": None, "approval_required": False}
            trace, model = {"duration_ms": 0, "spent_tokens": 0, "failures": 1}, {"name": os.getenv("LLM_MODEL"), "calls": 0}
            success, failure = False, type(exc).__name__
        reward = trajectory_reward(label_by_id[incident_id], prediction, trace, model, success)
        policy.update(state, route, reward["total"])
        episodes.append({
            "task_id": result.get("task_id") if success else None,
            "incident_id": incident_id, "context": state, "route": route,
            "prediction": prediction, "success": success, "failure_type": failure,
            "trace": {key: trace.get(key) for key in ("events", "duration_ms", "spent_tokens", "input_tokens", "cached_input_tokens", "output_tokens", "retries", "failures")},
            "reward": reward,
        })
    inputs = sum(item["trace"].get("input_tokens") or 0 for item in episodes)
    outputs = sum(item["trace"].get("output_tokens") or 0 for item in episodes)
    cached = sum(item["trace"].get("cached_input_tokens") or 0 for item in episodes)
    model_name = os.getenv("LLM_MODEL")
    rates = active_deepseek_rates() if model_name and "deepseek" in model_name.lower() else None
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    result = {
        "experiment": "train_contextual_bandit_routing_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "algorithm": "epsilon-greedy contextual bandit with sample-mean Q updates",
        "split": "train", "episodes": len(episodes), "policy": policy.to_dict(),
        "split_manifest": manifest["splits"]["train"], "rates": rates,
        "route_counts": {route: sum(item["route"] == route for item in episodes) for route in policy.to_dict()["routes"]},
        "mean_reward": sum(item["reward"]["total"] for item in episodes) / len(episodes) if episodes else None,
        "token_usage": {"input": inputs, "cached_input": cached, "output": outputs, "total": inputs + outputs},
        "model": model_name, "estimated_cost_usd": estimate_cost(model_name, inputs, cached, outputs, rates),
        "episodes_log": episodes,
        "notes": ["Each episode selects one route and updates its state/action value from observed reward.", "Training labels never enter model evidence context; the current case is excluded from historical-memory retrieval.", "No neural model weights or prompts are updated."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    result = train()
    print(json.dumps({"episodes": result["episodes"], "route_counts": result["route_counts"], "policy": result["policy"], "estimated_cost_usd": result["estimated_cost_usd"]}, ensure_ascii=False, indent=2))
    return 0 if result["episodes"] == 24 else 1


if __name__ == "__main__":
    raise SystemExit(main())
