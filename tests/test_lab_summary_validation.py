from pathlib import Path

from scripts.validate_lab_summary import ROOT, validate


def test_historical_strategy_summary_recomputes_from_case_reports():
    assert validate(ROOT / "reports/lab-strategy-summary.json", ROOT / "reports") == []


def test_summary_validation_rejects_non_live_or_missing_failure_metrics(tmp_path):
    import json
    summary = json.loads((ROOT / "reports/lab-strategy-summary.json").read_text(encoding="utf-8"))
    summary["live_experiment"] = True
    del summary["strategy_metrics"]["rules"]["failure_rate"]
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    errors = validate(path, ROOT / "reports")
    assert any("historical and non-live" in error for error in errors)
    assert any("failure_rate" in error for error in errors)


def test_summary_rejects_scoring_all_failed_multi_and_wrong_cost(tmp_path):
    import json
    summary = json.loads((ROOT / "reports/lab-strategy-summary.json").read_text(encoding="utf-8"))
    summary["strategy_metrics"]["multi"]["top1_rate"] = 0.0
    summary["strategy_metrics"]["single"]["provider_rate_estimate_usd"] = 99
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    errors = validate(path, ROOT / "reports")
    assert any("multi: top1_rate must be null" in error for error in errors)
    assert any("single: provider_rate_estimate_usd differs" in error for error in errors)
