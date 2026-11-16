from pathlib import Path
import json
import subprocess
import sys

from lab.slow_sql_evidence import verify_slow_sql_evidence


ROOT = Path(__file__).resolve().parents[1]


def test_isolated_slow_sql_runner_bounds_project_and_retains_volumes():
    runner = (ROOT / "lab" / "run-isolated-slow-sql.ps1").read_text(encoding="utf-8-sig")
    assert '"opspilot-slow-sql-$([guid]::NewGuid()' in runner
    assert "$env:COMPOSE_PROJECT_NAME = $projectName" in runner
    assert '$env:OPSPILOT_COMPOSE_OVERRIDE = $composeOverride' in runner
    assert "Required lab port $port is already in use" in runner
    assert "docker image inspect $image" in runner
    assert "docker build --pull=false --network=none" in runner
    assert "-OutputFile $resolvedOutput" in runner
    assert "Set-Content -LiteralPath $resolvedOutput" in runner
    assert "Refusing to overwrite existing report" in runner
    assert "docker compose --project-name $projectName" in runner
    assert " down`n" in runner or " down\n" in runner
    assert "down -v" not in runner
    assert "docker volume rm" not in runner
    assert "containers_empty = $cleanupVerified" in runner
    assert "volumes_deleted = $false" in runner
    assert "if (-not $cleanupVerified) { throw" in runner
    assert "truth.json" not in runner
    assert "run-vertical-slice.ps1" in runner


def test_slow_sql_runner_correlates_windowed_order_logs_with_query_trace():
    runner = (ROOT / "lab" / "run-isolated-slow-sql.ps1").read_text(encoding="utf-8-sig")
    assert '$result.fault_telemetry' in runner
    assert '$faultTelemetry.correlated -and $orderLogs.Count -gt 0 -and $correlated.Count -gt 0' in runner
    vertical = (ROOT / "lab" / "run-vertical-slice.ps1").read_text(encoding="utf-8-sig")
    assert "/logs?start=$from&end=$to&limit=1000" in vertical
    assert "/api/traces?service=orders&start=$jaegerStart&end=$jaegerEnd&limit=1000" in vertical
    assert '"http://127.0.0.1:16686/api/traces/$traceId"' in vertical
    assert "raw_jaeger_response = $rawJaegerResponse" in vertical
    assert "collector_status = $collectorStatus" in vertical
    assert "logs --no-color --tail 100 --since 2m otel-collector" in vertical
    assert "logs --no-color --tail 100 --since 2m jaeger" in vertical
    assert 'logs = $logResponse' in vertical and 'traces = $traceResponse' in vertical
    assert '& $python $evidenceVerifier' in vertical
    assert vertical.index("# Query volatile orders logs") < vertical.index("# Recovery:")
    assert 'recovery_request_order_id' in vertical
    assert "root_causes" not in runner


def test_slow_sql_export_flush_and_acceptance_keep_telemetry_failure_distinct():
    app = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
    runner = (ROOT / "lab" / "run-isolated-slow-sql.ps1").read_text(encoding="utf-8-sig")
    helper = (ROOT / "lab" / "service" / "trace_flush.py").read_text(encoding="utf-8")
    assert "provider.force_flush(timeout_millis=5000)" in helper
    assert app.index("if not flush_fault_trace(TRACE_PROVIDER):") < app.index("self.send_json(status, result)")
    assert 'status = if (-not $requestSucceeded) { "slow_request_failed" } elseif (-not $recoverySucceeded) { "recovery_failed" } elseif (-not $evidencePassed) { "telemetry_evidence_missing" } else { "passed" }' in runner
    assert "raw_jaeger_response = $faultTelemetry.raw_jaeger_response" in runner
    assert "collector_status = $faultTelemetry.collector_status" in runner
    assert "Slow SQL request and recovery were recorded separately; telemetry evidence missing:" in runner


def test_isolated_runner_persists_failed_cleanup_or_recovery_before_exiting_nonzero():
    runner = (ROOT / "lab" / "run-isolated-slow-sql.ps1").read_text(encoding="utf-8-sig")
    report_write = runner.index('Set-Content -LiteralPath $resolvedOutput')
    cleanup_failure = runner.index('if (-not $cleanupVerified) { throw')
    evidence_failure = runner.index('if (-not $evidencePassed) { throw')
    acceptance_failure = runner.index('if ($result.acceptance.status -ne "passed") { throw')

    assert report_write < cleanup_failure < evidence_failure < acceptance_failure


def test_fault_trace_flush_uses_bounded_provider_flush():
    from lab.service.trace_flush import flush_fault_trace

    class Provider:
        timeout = None

        def force_flush(self, timeout_millis):
            self.timeout = timeout_millis
            return True

    provider = Provider()
    assert flush_fault_trace(provider) is True
    assert provider.timeout == 5000
    provider.force_flush = lambda **_kwargs: False
    assert flush_fault_trace(provider) is False

    def fail_flush(**_kwargs):
        raise RuntimeError("export failed")

    provider.force_flush = fail_flush
    assert flush_fault_trace(provider) is False
    assert flush_fault_trace(None) is True


def test_compose_keeps_cached_service_images_stable_under_unique_project_names():
    compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
    for service in ("gateway", "orders", "inventory", "payment"):
        assert f"image: opspilot-lab-{service}:latest" in compose


def test_slow_sql_overlay_refreshes_orders_code_without_network_or_dependency_install():
    dockerfile = (ROOT / "lab" / "service" / "Dockerfile.slow-sql").read_text(encoding="utf-8")
    override = (ROOT / "lab" / "compose.slow-sql.yaml").read_text(encoding="utf-8")
    assert dockerfile.splitlines() == [
        "FROM opspilot-lab-orders:latest", "WORKDIR /app", "COPY app.py ./app.py", "COPY trace_flush.py ./trace_flush.py",
    ]
    assert "image: opspilot-lab-orders-slow-sql:latest" in override


def test_vertical_slice_applies_overlay_to_every_compose_operation():
    vertical = (ROOT / "lab" / "run-vertical-slice.ps1").read_text(encoding="utf-8-sig")
    assert '$composeFiles += @("-f", $env:OPSPILOT_COMPOSE_OVERRIDE)' in vertical
    assert vertical.count("docker compose @composeFiles") == 8
    assert "docker compose -f $composeFile" not in vertical


def test_orders_logs_are_volatile_and_collection_precedes_process_recreation():
    app = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
    compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
    vertical = (ROOT / "lab" / "run-vertical-slice.ps1").read_text(encoding="utf-8-sig")
    assert "LOGS: deque[dict[str, Any]] = deque(maxlen=5000)" in app
    assert "LOGS.append(item)" in app
    assert "rows = list(LOGS)" in app
    orders = compose.split("\n  orders:\n", 1)[1].split("\n  inventory:\n", 1)[0]
    assert "volumes:" not in orders
    assert vertical.index("# Query volatile orders logs") < vertical.index("# Recovery:")


def test_recorded_runtime_failure_with_empty_telemetry_never_counts_as_evidence():
    fixture = json.loads((ROOT / "tests" / "fixtures" / "op1_slow_sql_no_telemetry.json").read_text(encoding="utf-8"))
    assert fixture["timeline"]["fault_response_status"] == 200
    assert fixture["timeline"]["fault_request_duration_ms"] >= 1200
    assert fixture["timeline"]["recovery_response_status"] == 200
    assert fixture["evidence"]["logs"] == []
    assert fixture["evidence"]["trace_ids_queried"] == []
    assert fixture["evidence"]["traces"] == []
    assert fixture["evidence"]["correlated"] is False
    assert fixture["cleanup"]["containers_empty"] is True
    assert fixture["cleanup"]["volumes_deleted"] is False
    checked = verify_slow_sql_evidence({
        "order_id": "synthetic-failed-order",
        "window_start": "2026-11-16T05:23:34Z",
        "window_end": "2026-11-16T05:23:40Z",
        "logs": {"records": fixture["evidence"]["logs"]},
        "traces": {"data": fixture["evidence"]["traces"]},
    })
    assert checked["correlated"] is False
    assert checked["failure_stage"] == "no_order_logs"
    assert checked["error"] == "No slow postgres.query span correlated; stage=no_order_logs, logs=0, spans=0."


def test_slow_sql_span_export_path_and_trace_tags_match_query_contract():
    app = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
    collector = (ROOT / "lab" / "otel" / "collector.yaml").read_text(encoding="utf-8")
    compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "lab" / "service" / "Dockerfile").read_text(encoding="utf-8")
    vertical = (ROOT / "lab" / "run-vertical-slice.ps1").read_text(encoding="utf-8-sig")
    verifier = (ROOT / "lab" / "slow_sql_evidence.py").read_text(encoding="utf-8")
    assert 'start_as_current_span("postgres.query")' in app
    assert 'span.set_attribute("opspilot.order_id", order_id)' in app
    assert 'trace_id=request_trace_id' in app
    assert 'OTLPSpanExporter(endpoint=f"{OTLP.rstrip(\'/\')}/v1/traces")' in app
    assert 'schedule_delay_millis=500' in app
    assert "receivers: [otlp]" in collector and "exporters: [otlp/jaeger]" in collector
    assert "endpoint: jaeger:4317" in collector
    assert "OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318" in compose
    assert "COPY trace_flush.py ./trace_flush.py" in dockerfile
    assert 'COLLECTOR_OTLP_ENABLED: "true"' in compose
    assert "/api/traces?service=orders&start=$jaegerStart&end=$jaegerEnd&limit=1000" in vertical
    assert vertical.index("Start-Sleep -Seconds 4", vertical.index("$faultRequestCompleted =")) < vertical.index("$traceSearchResponse =")
    assert '"opspilot.order_id"' in verifier and '"postgres.query"' in verifier
    assert 'in_window(span_start, start, end)' in verifier


def test_actual_shape_log_and_jaeger_payload_correlates_by_window_order_and_trace():
    payload = json.loads((ROOT / "tests" / "fixtures" / "op1_slow_sql_api_payload.json").read_text(encoding="utf-8"))
    completed = subprocess.run(
        [sys.executable, str(ROOT / "lab" / "slow_sql_evidence.py")],
        input=json.dumps(payload), capture_output=True, text=True, cwd=ROOT, check=True,
    )
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["correlated"] is True
    assert result["failure_stage"] is None
    assert [row["message"] for row in result["logs"]] == ["request completed"]
    assert result["trace_ids"] == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert len(result["traces"]) == 2
    linked_query = [row for row in result["traces"]
                    if row["operation"] == "postgres.query" and row["trace_id"] in result["trace_ids"]]
    assert len(linked_query) == 1
    assert linked_query[0]["linked_to_log"] is True
    assert linked_query[0]["duration_us"] == 1_500_000
    assert linked_query[0]["order_id"] == payload["order_id"]
    unlinked = next(row for row in result["traces"] if row["trace_id"].startswith("b"))
    assert unlinked["linked_to_log"] is False
    assert result["raw_jaeger_response"] == payload["traces"]


def test_non_orders_log_cannot_correlate_slow_sql_with_orders_span():
    payload = json.loads((ROOT / "tests" / "fixtures" / "op1_slow_sql_api_payload.json").read_text(encoding="utf-8"))
    payload["logs"]["records"][0]["service"] = "gateway"

    result = verify_slow_sql_evidence(payload)

    assert result["correlated"] is False
    assert result["failure_stage"] == "no_order_logs"
    assert result["logs"] == []


def test_trace_id_without_exported_span_is_distinct_from_missing_logs():
    payload = json.loads((ROOT / "tests" / "fixtures" / "op1_slow_sql_api_payload.json").read_text(encoding="utf-8"))
    payload["traces"] = {
        "window_query": {"data": []},
        "by_trace_id": [{"trace_id": payload["logs"]["records"][0]["trace_id"],
                         "status": 200, "response": {"data": []}}],
    }
    result = verify_slow_sql_evidence(payload)
    assert result["logs"]
    assert result["trace_ids"] == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert result["traces"] == []
    assert result["failure_stage"] == "jaeger_no_order_spans"
    assert result["raw_jaeger_response"] == payload["traces"]


def test_trace_id_lookup_can_recover_slow_span_missing_from_window_search():
    payload = json.loads((ROOT / "tests" / "fixtures" / "op1_slow_sql_api_payload.json").read_text(encoding="utf-8"))
    found = payload["traces"]["data"][0]
    payload["traces"] = {
        "window_query": {"data": []},
        "by_trace_id": [{"trace_id": found["traceID"], "status": 200, "response": {"data": [found]}}],
    }
    result = verify_slow_sql_evidence(payload)
    assert result["correlated"] is True
    assert result["traces"][0]["operation"] == "postgres.query"
