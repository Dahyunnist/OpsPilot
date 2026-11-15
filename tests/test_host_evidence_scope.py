from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _report(name: str) -> dict:
    return json.loads((ROOT / "reports" / name).read_text(encoding="utf-8"))


def test_manual_bad_release_report_is_not_attributed_to_new_verification_script():
    report = _report("op1-bad-release-manual-host-2026-11-15.json")
    assert report["invocation"] == ".\\lab\\run-scenario.ps1 -Scenario bad_release"
    assert report["verification_script"]["used_for_this_evidence"] is False
    assert report["checkout"]["http_status"] == 502
    assert report["logs"]["records_returned"] is True
    assert report["deployments"]["orders_v2_record_returned"] is True
    assert report["cleanup"]["containers_empty_after_stop"] is True
    assert report["logs"]["order_id_correlation_verifiable"] is False
    assert report["deployments"]["exact_window_verifiable"] is False


def test_provider_preflight_block_is_no_retry_and_no_inference():
    report = _report("op3-provider-preflight-host-blocked-2026-11-15.json")
    assert report["run_id"] is None
    assert report["request"]["retry_attempted"] is False
    assert report["outcome"]["error_type"] == "URLError"
    assert report["outcome"]["error_code"] == "WinError 10013"
    assert report["outcome"]["provider_response_received"] is False
    assert report["outcome"]["inference_started"] is False
    assert report["outcome"]["model_calls"] == 0
    assert report["outcome"]["new_cost_usd"] == 0
    assert report["outcome"]["ledger_write"] is None


def test_cny_opspilot_amount_is_limited_to_exact_csv_window():
    ledger_path = ROOT / "reports" / "provider-billing-reconciliation-ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ledger = next(row for row in records if row["kind"] == "provider_export_reconciliation")
    assert ledger["export_window"]["start"] == "2026-10-17T00:00:00+08:00"
    assert ledger["export_window"]["end_exclusive"] == "2026-11-15T00:00:00+08:00"
    assert ledger["currency"] == "CNY"
    assert ledger["opspilot_key"]["derived_cost_cny"] == 3.79653708
    assert ledger["account_and_key_totals_are_additive"] is False
    assert ledger["usd_api_cost_ledger"]["fx_conversion_applied"] is False
    assert ledger["raw_csv_copied_to_repository"] is False
