"""Aggregate per-scenario fixed-snapshot strategy comparisons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.strategy_eval import summarize_cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize OpsPilot live lab strategy reports")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.input]
    case_ids = [report["case_id"] for report in reports]
    if len(case_ids) != len(set(case_ids)):
        raise SystemExit("duplicate case IDs")
    report = {"cases": case_ids, "model_ids": sorted({item.get("model", {}).get("id") for item in reports if item.get("model", {}).get("id")}),
        "strategy_metrics": summarize_cases(reports),
        "cumulative_peak_budget_usd": max((item.get("budget", {}).get("cumulative_peak_estimate_usd", 0) for item in reports), default=0),
        "budget_limit_usd": 0.50,
        "failure_cases": {strategy: [{"case_id": row["case_id"], "reasons": row["strategies"][strategy]["failures"]}
            for row in reports if row.get("strategies", {}).get(strategy, {}).get("failures")]
            for strategy in ("rules", "single", "multi", "auto")},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["strategy_metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
