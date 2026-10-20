"""Replay validation-only route decisions against already measured strategies."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.cli import load_incidents  # noqa: E402
from opspilot.investigator import investigate  # noqa: E402
from opspilot.routing import select_route  # noqa: E402
from scripts.evaluate_agents import active_deepseek_rates, summarize  # noqa: E402


def evaluate() -> dict:
    data_dir = ROOT / "data" / "complex-benchmark"
    measured_path = ROOT / "reports" / "complex-validation-evaluation.json"
    measured = json.loads(measured_path.read_text(encoding="utf-8"))
    if measured.get("evaluated_split") != "validation" or measured.get("evaluation") != "opspilot_complex_conflicts_v1":
        raise ValueError("route replay requires completed complex validation evaluation")
    incidents = load_incidents(data_dir / "validation.jsonl")
    by_strategy = {strategy: {case["incident_id"]: case for case in cases}
                   for strategy, cases in measured["cases"].items()}
    selected = []
    routes = {}
    for incident_id, incident in sorted(incidents.items()):
        baseline = investigate(incident, query_budget=8)
        strategy, reason = select_route(baseline)
        case = dict(by_strategy[strategy][incident_id])
        case["route_reason"] = reason
        selected.append(case)
        routes[strategy] = routes.get(strategy, 0) + 1
    return {
        "evaluation": "complex_validation_route_replay_v1",
        "source_evaluation": str(measured_path.relative_to(ROOT)),
        "split": "validation",
        "case_count": len(incidents),
        "route_counts": routes,
        "routed_summary": summarize(selected, measured.get("rates") or active_deepseek_rates()),
        "fixed_strategy_validation_summaries": measured["summaries"],
        "cases": [{"incident_id": case["incident_id"], "selected_strategy": "single-agent" if case["model"].get("calls", 0) else "rules",
                   "reason": case["route_reason"], "success": case["success"],
                   "root_cause": case.get("prediction", {}).get("root_cause")} for case in selected],
        "interpretation": "The route is selected from live evidence features. Replay reuses validation predictions; it makes no new model calls and does not evaluate test-time routing.",
    }


def main() -> int:
    result = evaluate()
    output = ROOT / "reports" / "complex-routing-validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"route_counts": result["route_counts"], "summary": result["routed_summary"]}, ensure_ascii=False, indent=2))
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
