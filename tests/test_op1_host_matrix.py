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
    assert script.index("docker image inspect $image") < script.index('$env:COMPOSE_PROJECT_NAME = "opspilot-matrix-')
    assert "Get-NetTCPConnection -State Listen -LocalPort $port" in script
    assert "finally" in script and "stop.ps1" in script and "ps --all" in script
    assert "$env:COMPOSE_PROJECT_NAME = \"opspilot-matrix-" in script
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
