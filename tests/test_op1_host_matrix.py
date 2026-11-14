from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_host_matrix_is_isolated_continues_cases_and_never_deletes_volumes():
    script = (ROOT / "lab/run-host-matrix.ps1").read_text(encoding="utf-8-sig")
    scenario_runner = (ROOT / "lab/run-scenario.ps1").read_text(encoding="utf-8-sig")
    stop = (ROOT / "lab/stop.ps1").read_text(encoding="utf-8-sig")
    compose = (ROOT / "lab/compose.yaml").read_text(encoding="utf-8")
    truth = (ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8")
    assert "foreach ($case in $truth.cases)" in script
    assert "opspilot-matrix-" in script and "Set-Content -Encoding utf8 $OutputFile" in script
    assert "docker info --format" in script and "docker image inspect $image" in script
    assert "Missing local image; refusing network pull" in script and "config --format json" in script
    assert "foreach ($port in @(18080, 18081, 18082, 18083, 14318, 16686, 19090))" in script
    assert script.index("docker info --format") < script.index("$runScript -Scenario none")
    assert script.index("docker image inspect $image") < script.index("$env:COMPOSE_PROJECT_NAME = $runId")
    assert "Get-NetTCPConnection -State Listen -LocalPort $port" in script
    assert "finally" in script and "stop.ps1" in script and "ps --all" in script
    assert "$env:COMPOSE_PROJECT_NAME = $runId" in script
    assert script.index("$env:COMPOSE_PROJECT_NAME =") < script.index("foreach ($case in $truth.cases)")
    assert scenario_runner.count("docker compose @composeFiles up --build") == 1
    assert "function Wait-LabReady([int]$TimeoutSeconds = 60)" in scenario_runner
    assert scenario_runner.count("\nWait-LabReady\n") == 1
    assert scenario_runner.index("\nWait-LabReady\n") < scenario_runner.index('Write-Host "Scenario ready:')
    assert "docker compose @composeFiles down" in stop
    assert "-v" not in stop and "docker volume rm" not in stop
    assert "pgdata:/var/lib/postgresql/data" in compose and "redisdata:/data" in compose
    assert "volume_deletion=$false" in script
    assert re.search(r"(?:^|\s)-v(?:\s|$)", script) is None and "docker volume rm" not in script
    assert "$preflightResult | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 $OutputFile" in script
    assert "lab_started=$false" in script and "lab_started=$true" in script
    assert "Write-Op1RunManifestArtifact" in script and "op1-run-manifest.ps1" in script
    assert "$manifestPath = [IO.Path]::ChangeExtension($OutputFile, \".run-manifest.json\")" in script
    for field in ("project", "preflight", "normal_control", "cases", "cleanup", "volume_deletion"):
        assert f"{field}=" in script
    assert "model_client" not in script.lower() and "chatmodel" not in script.lower()
    assert truth.count('"case_id"') == 12
    stage_doc = (ROOT / "docs/learning/stage-OP1.md").read_text(encoding="utf-8")
    assert "当前列出十二个" in stage_doc
    assert "run-host-matrix.ps1" in stage_doc and "docker compose up --build" in stage_doc
    assert r".\lab\run-host-matrix.ps1 -OutputFile .tmp\op1-host-matrix-host.json" in stage_doc
    assert ".tmp/op1-host-matrix-host.json" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "本执行环境无法连接 Docker Desktop Linux Engine" in stage_doc
    assert '18081/logs?start=$from&end=$settledTo' in script
    assert '18080/deployments?start=$from&end=$to' in script
    assert "normal_control=$normalControl" in script and "-Scenario none" in script
    assert "$priorLabFault = $env:LAB_FAULT" in script
    assert "$env:LAB_FAULT=$priorLabFault" in script
    assert "expected_deployment_observed" in script and "scenario_passed=$scenarioPassed" in script
    assert "$telemetryObserved=$logsObserved -and $tracesObserved" in script
    assert "logs_observed=$logsObserved" in script and "traces_observed=$tracesObserved" in script
    assert "Test-MatrixScenarioPass" in script
    assert "Get-ObservedSignals" in script
    assert "foreach ($port in @(18081,18082,18083))" in script
    assert "$start $end $id" in script
    assert '$start = [DateTime]::UtcNow.ToString("o")' in script
    assert "for ($traceAttempt=0; $traceAttempt -le 10; $traceAttempt++)" in script
    assert "api/traces?service=orders&limit=1000" in script
    assert "$missingTraceSignals.Count -eq 0 -and $missingLogSignals.Count -eq 0" in script
    assert "$windowSpans.Count -gt 0 -and $missingTraceSignals.Count -eq 0" in script
    assert "window_log_records=$safeLogs" in script and "window_trace_spans=$safeSpans" in script
    assert "$settleAttempts=if ($case.fault -eq \"bad_release\") { 10 } else { 2 }" in script
    assert "for ($settle=0; $settle -le $settleAttempts; $settle++)" in script
    assert "@(502, 504)" in script and "expected_http_statuses=$expectedStatuses" in script


def test_every_truth_signal_has_a_report_mapping():
    import json

    truth = json.loads((ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8"))
    contract = (ROOT / "lab/op1-matrix-contract.ps1").read_text(encoding="utf-8")
    signals = {signal for case in truth["cases"] for signal in case["must_observe"]}
    for signal in signals:
        assert f'"{signal}" {{' in contract
    matrix = (ROOT / "lab/run-host-matrix.ps1").read_text(encoding="utf-8-sig")
    assert "signal_results=$signals" in matrix
    assert "missing_signals=$missingSignals" in matrix


def test_user_host_bad_release_smoke_does_not_claim_full_matrix_or_agent_diagnosis():
    import json

    evidence = json.loads((ROOT / "reports/op1-bad-release-host-smoke-2026-11-14.json").read_text(encoding="utf-8"))
    assert evidence["scenario"] == "bad_release"
    assert evidence["environment"]["kind"] == "synthetic_local_docker_compose"
    assert evidence["environment"]["production_evidence"] is False
    assert evidence["checkout"]["http_status"] == 502
    assert (evidence["logs"]["host"], evidence["logs"]["path"]) == ("127.0.0.1:18081", "/logs")
    assert (evidence["deployments"]["host"], evidence["deployments"]["path"]) == ("127.0.0.1:18080", "/deployments")
    assert evidence["window_utc"] == {
        "interval": "[start,end)", "shared_by_logs_and_deployments": True,
        "exact_start": None, "exact_end": None, "absolute_bounds_recorded": False,
    }
    assert evidence["logs"]["record_returned_within_reported_window"] is True
    assert evidence["deployments"]["record_returned_within_reported_window"] is True
    assert evidence["deployments"]["orders_v2_record_returned"] is True
    assert evidence["cleanup"]["stop_status"] == "passed"
    assert evidence["cleanup"]["containers_removed"] == 10
    assert evidence["cleanup"]["networks_removed"] == 1
    assert evidence["cleanup"]["compose_ps_all_empty"] is True
    assert evidence["full_matrix_run"] is False
    assert evidence["agent_root_cause_diagnosis_verified"] is False
    assert evidence["logs"]["order_id_correlation_verifiable"] is False


def test_op3_run_report_ledger_and_frozen_manifest_consistency_audit():
    import hashlib
    import json

    report_path = ROOT / "reports/op3-op2-three-case-run-2026-11-14-r2.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    audit = json.loads((ROOT / "reports/op3-three-case-run-consistency-audit-2026-11-14.json").read_text(encoding="utf-8"))
    failure_audit = json.loads((ROOT / "reports/op3-three-case-run-failure-audit-2026-11-14.json").read_text(encoding="utf-8"))
    manifest_path = ROOT / "reports/op2-model-subset.json"
    ledger = [json.loads(line) for line in (ROOT / "reports/api-cost-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    run_rows = [row for row in ledger if row.get("run_id") == report["run_id"]]
    requests = [row for row in run_rows if row.get("kind") in {"completed_request", "failed_request_worst_case"}]
    summary = next(row for row in run_rows if row.get("kind") == "run_summary")
    key = lambda row: tuple(row.get("request_metadata", {}).get(field) for field in
        ("case_id", "system_sha256", "user_payload_sha256", "token_budget"))
    request_peak = round(sum(row.get("peak_rate_estimate_usd", row.get("reserve_usd", 0)) for row in requests), 8)

    assert report["evaluation_status"] == "partial"
    assert report["strategy_summary"]["multi"]["failed_cases"] == 3
    assert len(report["strategy_summary"]["multi"]["failures"]) == 3
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == audit["source_report"]["sha256"]
    assert failure_audit["source_report_sha256"] == audit["source_report"]["sha256"]
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == report["frozen_dataset"]["spec_sha256"]
    assert audit["run_manifest"]["separate_live_run_artifact_present"] is False
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in report["code_sha256"].values())
    assert len(requests) == 9
    assert len({key(row) for row in requests}) == len(requests)
    assert request_peak == summary["peak_rate_estimate_usd"] == report["budget"]["actual_run_peak_usd"]
    assert summary["cumulative_peak_estimate_usd"] == report["budget"]["cumulative_peak_usd"]
    assert audit["ledger_consistency"]["sum_request_peak_usd"] == request_peak
    assert summary["provider_rate_estimate_usd"] == report["strategy_summary"]["multi"]["provider_rate_estimate_usd"]
    assert audit["ledger_consistency"]["provider_rate_estimate_delta_from_peak_usd"] == -0.00000001


def test_matrix_contract_rejects_missing_logs_or_trace_spans():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required to execute the host matrix contract")
    contract_path = str(ROOT / "lab/op1-matrix-contract.ps1").replace("'", "''")
    command = (
        f". '{contract_path}'; "
        "$logs=@([pscustomobject]@{record_id='log-1'}); "
        "$spans=@([pscustomobject]@{operation='POST /orders'}); "
        "$signals=@([pscustomobject]@{observed=$true}); "
        "$complete=Test-MatrixScenarioPass @{ExpectedStatusObserved=$true;VersionObserved=$true;DnsObserved=$true;Logs=$logs;TraceSpans=$spans;SignalResults=$signals}; "
        "$missingLogs=Test-MatrixScenarioPass @{ExpectedStatusObserved=$true;VersionObserved=$true;DnsObserved=$true;Logs=@();TraceSpans=$spans;SignalResults=$signals}; "
        "$missingTraces=Test-MatrixScenarioPass @{ExpectedStatusObserved=$true;VersionObserved=$true;DnsObserved=$true;Logs=$logs;TraceSpans=@();SignalResults=$signals}; "
        "[pscustomobject]@{complete=$complete;missing_logs=$missingLogs;missing_traces=$missingTraces}|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, check=True, timeout=15,
    )
    import json

    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcome == {"complete": True, "missing_logs": False, "missing_traces": False}


def test_all_scenario_signals_match_order_scoped_log_and_trace_fixtures():
    import base64
    import datetime as dt
    import json

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required to execute the host matrix contract")
    truth = json.loads((ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8"))
    order_id = "matrix-fixture-001"
    logs_by_signal = {
        "orders_db_errors": {"service": "orders", "severity": "error", "message": "request failed", "error_type": "OperationalError"},
        "db_session_pressure": {"service": "orders", "severity": "warning", "message": "database sessions remain occupied"},
        "orders_timeout": {"service": "orders", "severity": "warning", "message": "payment attempt failed", "error_type": "TimeoutError"},
        "inventory_connection_errors": {"service": "orders", "severity": "error", "message": "inventory dependency failed", "dependency_host": "inventory-v2", "error_reason": "Name or service not known"},
        "payment_attempts": {"service": "payment", "severity": "warning", "message": "request returned an error response", "status": 503},
        "orders_retry_logs": {"service": "orders", "severity": "warning", "message": "payment attempt failed", "error_type": "HTTPError"},
        "payment_503": {"service": "payment", "severity": "warning", "message": "request returned an error response", "status": 503},
        "orders_payment_attempt_failure": {"service": "orders", "severity": "warning", "message": "payment attempt failed", "error_type": "HTTPError"},
        "inventory_stale_replica_response": {"service": "inventory", "severity": "warning", "message": "inventory stale replica response", "remaining": 20},
        "inventory_remaining_20": {"service": "inventory", "severity": "warning", "message": "inventory stale replica response", "remaining": 20},
        "duplicate_delivery_replayed_payment": {"service": "orders", "severity": "error", "message": "duplicate delivery replayed payment"},
        "same_order_id": {"service": "orders", "severity": "error", "message": "duplicate delivery replayed payment"},
        "cache_connection_failed": {"service": "orders", "severity": "error", "message": "cache connection failed"},
        "orders_cache_error": {"service": "orders", "severity": "error", "message": "request failed", "error_type": "ConnectionError"},
        "inventory_remaining_zero": {"service": "inventory", "severity": "warning", "message": "inventory exhausted", "remaining": 0},
        "reservation_rejected": {"service": "orders", "severity": "warning", "message": "inventory reservation rejected"},
    }
    start = dt.datetime(2026, 11, 13, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(hours=1)
    logs = []
    for signal, row in logs_by_signal.items():
        repeated = 3 if signal in {"payment_attempts", "orders_retry_logs"} else 1
        logs.extend(dict(row, record_id=f"log-{signal}-{index}", order_id=order_id)
                    for index in range(repeated))
    for index, row in enumerate(logs):
        row["timestamp"] = (start + dt.timedelta(seconds=5, microseconds=index)).isoformat().replace("+00:00", "Z")
    logs.append(dict(logs_by_signal["orders_db_errors"], record_id="out-of-window", order_id=order_id,
                     timestamp=(start - dt.timedelta(seconds=1)).isoformat().replace("+00:00", "Z")))
    start_us = int(start.timestamp() * 1_000_000)
    process_ids = {"payment": "p1", "orders": "p2", "inventory": "p3"}
    spans = []
    for service, operation, duration in (
        ("payment", "payment/charge", 600_000),
        ("orders", "orders/orders", 800_000),
        ("inventory", "inventory/reserve", 800_000),
        ("orders", "postgres.query", 100_000),
    ):
        spans.append({"processID": process_ids[service], "operationName": operation,
                      "spanID": f"span-{len(spans)}", "startTime": start_us + 1_000_000, "duration": duration,
                      "tags": [{"key": "opspilot.order_id", "type": "string", "value": order_id}]})
    fixture = {"cases": truth["cases"], "order_id": order_id, "logs": logs,
               "deployments": [{"service": "orders", "version": "v2", "timestamp": (start + dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")}],
               "traces": [{"traceID": "trace-fixture-001", "processes": {pid: {"serviceName": service}
                                            for service, pid in process_ids.items()}, "spans": spans}]}
    encoded = base64.b64encode(json.dumps(fixture).encode()).decode()
    contract_path = str(ROOT / "lab/op1-matrix-contract.ps1").replace("'", "''")
    command = (
        f". '{contract_path}'; "
        f"$fixture=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))|ConvertFrom-Json; "
        f"$start='{start.isoformat().replace('+00:00', 'Z')}'; $end='{end.isoformat().replace('+00:00', 'Z')}'; "
        "$all=@(); foreach ($case in $fixture.cases) { "
        "$all+=@(Get-ObservedSignals $case $fixture.order_id $fixture.logs $fixture.deployments $fixture.traces $start $end) }; "
        "$absent=@(Get-ObservedSignals $fixture.cases[0] $fixture.order_id @() @() @() $start $end); "
        "[pscustomobject]@{all=$all;absent=$absent}|ConvertTo-Json -Depth 8 -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, check=True, timeout=15,
    )
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    observed = outcome["all"]
    assert len(observed) == 25
    assert all(item["observed"] for item in observed), [item["name"] for item in observed if not item["observed"]]
    assert all(item["evidence"] for item in observed)
    assert all(item["raw_matches"] for item in observed)
    assert all(item["query_scope"]["order_id"] == order_id for item in observed)
    assert all(record.get("order_id") == order_id for item in observed for record in item["raw_matches"] if item["query_scope"]["source"] != "deployments")
    assert all(not item["observed"] and item["raw_matches"] == [] and item["query_scope"]["start"] for item in outcome["absent"])
