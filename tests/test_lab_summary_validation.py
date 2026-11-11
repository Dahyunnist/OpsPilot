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
