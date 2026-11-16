from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "c1c10951-2b5d-40aa-a9c0-7f2d31aff5ef"
REPORT = ROOT / f"reports/op3-multiagent-authorized-replay-{RUN_ID}.json"
CHECKPOINT = ROOT / f"reports/op3-multiagent-authorized-replay-{RUN_ID}.requests.jsonl"
PLAN = ROOT / f"reports/op3-multiagent-authorized-replay-plan-{RUN_ID}.json"
SUMMARY = ROOT / f"reports/op3-multiagent-authorized-replay-summary-audit-{RUN_ID}.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sensitive_findings(value: object, path: str = "$", findings: list[str] | None = None) -> list[str]:
    findings = findings if findings is not None else []
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?i)(api.?key|password|client.?secret|private.?key)", str(key)):
                findings.append(f"{path}.{key}")
            _sensitive_findings(child, f"{path}.{key}", findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _sensitive_findings(child, f"{path}[{index}]", findings)
    elif isinstance(value, str) and re.search(
        r"(?i)\bBearer\s+\S+|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|"
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\b\d{3}-\d{2}-\d{4}\b",
        value,
    ):
        findings.append(f"{path}#sensitive-pattern")
    return findings


def test_partial_report_checkpoint_and_ledger_extract_reconcile_without_hiding_failures():
    report = _read_json(REPORT)
    checkpoint = [json.loads(line) for line in CHECKPOINT.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    plan = _read_json(PLAN)
    summary = _read_json(SUMMARY)
    ledger = _read_json(ROOT / "tests/fixtures/op3_partial_run_ledger_extract.json")

    assert report["run_id"] == summary["run_id"] == ledger["run_id"] == RUN_ID
    assert report["evaluation_status"] == summary["evaluation_status"] == "partial"
    assert summary["source_report_sha256"] == _sha256(REPORT)
    assert report["budget"]["actual_run_peak_usd"] == summary["actual_run_cost_usd"] == ledger["run_peak_usd"]
    assert report["budget"]["cumulative_peak_usd"] == ledger["cumulative_peak_usd"]
    assert report["budget"]["remaining_usd"] == ledger["remaining_usd"]
    assert plan["budget"]["ledger_sha256"] == ledger["ledger_sha256_at_authorized_plan"]
    assert round(plan["budget"]["latest_ledger_peak_usd"] + ledger["run_peak_usd"], 8) == ledger["cumulative_peak_usd"]
    assert round(ledger["cumulative_peak_usd"] + ledger["remaining_usd"], 8) == ledger["project_cap_usd"]
    assert report["budget"]["provider_request_attempts"] == summary["provider_request_attempts"] == ledger["request_slots_attempted"]
    assert report["budget"]["max_provider_requests"] == summary["max_provider_requests"] == ledger["max_request_slots"]
    assert report["request_checkpoint"] == CHECKPOINT.relative_to(ROOT).as_posix()

    billable = [row for row in checkpoint if row.get("status") in {"completed", "failed_or_blocked"}]
    assert len(billable) == ledger["provider_requests"]
    assert Counter(row["status"] for row in billable) == {
        "completed": ledger["completed_request_rows"],
        "failed_or_blocked": ledger["failed_request_worst_case_rows"],
    }
    assert ledger["request_slots_attempted"] - len(billable) == ledger["budget_rejections_before_provider"]
    assert sum(result["model_calls"] for case in report["per_case"]
               for result in case["strategies"].values()) == len(billable)

    cases = {item["case_id"]: item for item in report["per_case"]}
    assert set(cases) == {"normal_slow_sql", "missing_pool_evidence", "contradictory_payment"}
    expected = {"rules": 3, "single": 2, "multi": 1, "auto": 2}
    for strategy, completed in expected.items():
        result = summary["strategy_summary"][strategy]
        assert result["cases"] == 3
        assert result["completed_cases"] == completed
        assert result["failed_cases"] == 3 - completed
        assert result["performance_metric_denominator"] == "all cases; failed cases score zero"
        assert result["quality_metric_denominator"] == "completed cases only"

    errors = {
        (case_id, strategy, error)
        for case_id, case in cases.items()
        for strategy, result in case["strategies"].items()
        for failure in result["failures"]
        for error in ([failure] if isinstance(failure, str) else failure.get("reasons", []))
    }
    assert ("missing_pool_evidence", "multi", "response_schema:truncated_json") in errors
    assert ("contradictory_payment", "single", "BudgetLimitExceeded") in errors
    assert ("contradictory_payment", "multi", "BudgetLimitExceeded") in errors
    assert ("contradictory_payment", "auto", "BudgetLimitExceeded") in errors


def test_partial_run_is_limited_to_authorized_synthetic_inputs_and_has_no_credential_patterns():
    report = _read_json(REPORT)
    plan = _read_json(PLAN)
    spec_path = ROOT / "reports/op2-model-subset.json"
    spec = _read_json(spec_path)
    expected_cases = ["normal_slow_sql", "missing_pool_evidence", "contradictory_payment"]

    assert plan["status"] == "authorized_for_execution"
    assert plan["scope"]["spec_sha256"] == _sha256(spec_path)
    assert plan["scope"]["case_ids"] == expected_cases
    assert plan["scope"]["strategies"] == ["rules", "single", "multi", "auto"]
    assert plan["request_plan"]["max_provider_requests"] == 18
    data_safety = plan["data_safety"]
    assert data_safety.get("synthetic_frozen_snapshots_only") is True
    assert data_safety.get("gold_sent_to_provider") is False
    assert data_safety.get("secrets_or_credentials_written") is False
    assert data_safety.get("real_customer_data_sent") is False
    snapshot_hashes = {case["id"]: _sha256(ROOT / case["snapshot"]) for case in spec["cases"]}
    assert snapshot_hashes == plan["scope"]["snapshot_sha256"]
    assert {entry["case_id"] for entry in report["request_payload_audit"]} <= set(expected_cases)

    artifacts = (REPORT, CHECKPOINT, PLAN, SUMMARY)
    for artifact in artifacts:
        parsed = _read_json(artifact) if artifact != CHECKPOINT else [
            json.loads(line) for line in artifact.read_text(encoding="utf-8-sig").splitlines() if line.strip()
        ]
        assert _sensitive_findings(parsed) == []
