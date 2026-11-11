"""Validate the checked-in fixed-snapshot strategy summary against raw case reports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opspilot.strategy_eval import summarize_cases

STRATEGIES = ("rules", "single", "multi", "auto")


def validate(summary_path: Path, reports_dir: Path) -> list[str]:
    errors: list[str] = []
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(reports_dir.glob("lab-strategy-lab-*.json"))]
    ids = [case.get("case_id") for case in cases]
    if not cases or len(ids) != len(set(ids)):
        errors.append("raw case reports missing or duplicated")
        return errors
    if summary.get("cases") != ids:
        errors.append("summary case list does not match raw reports")
    metrics = summary.get("strategy_metrics", {})
    recomputed = summarize_cases(cases)
    for strategy in STRATEGIES:
        actual = metrics.get(strategy, {})
        expected = recomputed.get(strategy, {})
        if actual.get("cases") != len(cases):
            errors.append(f"{strategy}: sample count mismatch")
        for field in ("failed_cases", "exact_root_set_successes", "model_calls", "provider_rate_estimate_usd"):
            if field not in actual:
                errors.append(f"{strategy}: missing {field}")
            elif actual[field] != expected.get(field):
                errors.append(f"{strategy}: {field} differs from raw reports")
        if actual.get("failed_cases") is not None and actual["cases"]:
            if "failure_rate" not in actual or abs(actual["failure_rate"] - actual["failed_cases"] / actual["cases"]) > 1e-12:
                errors.append(f"{strategy}: invalid failure_rate")
    if summary.get("evidence_kind") != "historical_fixed_snapshot" or summary.get("live_experiment") is not False:
        errors.append("summary must be labeled historical and non-live")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=ROOT / "reports/lab-strategy-summary.json")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    errors = validate(args.summary, args.reports_dir)
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, "evidence_kind": "historical_fixed_snapshot", "live_experiment": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
