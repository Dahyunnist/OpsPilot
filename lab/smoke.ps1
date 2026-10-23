$ErrorActionPreference = "Stop"
$truthPath = Join-Path $PSScriptRoot "scenarios/truth.json"
$truth = Get-Content -LiteralPath $truthPath -Raw | ConvertFrom-Json
$runScript = Join-Path $PSScriptRoot "run-scenario.ps1"
$observed = @()

foreach ($case in $truth.cases) {
    & $runScript -Scenario $case.fault
    if ($LASTEXITCODE -ne 0) { throw "Scenario startup failed: $($case.case_id)" }

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:18081/health" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod -Uri "http://127.0.0.1:18082/health" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod -Uri "http://127.0.0.1:18083/ready" -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) { throw "Gateway did not become ready: $($case.case_id)" }

    $status = 200
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{ order_id = $case.case_id } | ConvertTo-Json) -TimeoutSec 8 | Out-Null
    } catch {
        $response = $_.Exception.Response
        if ($response -and $response.StatusCode) { $status = [int]$response.StatusCode } else { throw }
    }
    $mustSucceed = $case.fault -in @("slow_sql", "retry_storm")
    if ($mustSucceed -and $status -ne 200) { throw "Expected successful checkout: $($case.case_id); got HTTP $status" }
    if (-not $mustSucceed -and $status -lt 400) { throw "Expected injected failure: $($case.case_id); got HTTP $status" }
    Start-Sleep -Seconds 3

    $windowStart = [DateTime]::UtcNow.AddMinutes(-5).ToString("o")
    $windowEnd = [DateTime]::UtcNow.AddMinutes(1).ToString("o")
    $logs = Invoke-RestMethod -Uri "http://127.0.0.1:18081/logs?start=$([uri]::EscapeDataString($windowStart))&end=$([uri]::EscapeDataString($windowEnd))&limit=1000" -TimeoutSec 3
    $deployments = Invoke-RestMethod -Uri "http://127.0.0.1:18080/deployments?start=$([uri]::EscapeDataString($windowStart))&end=$([uri]::EscapeDataString($windowEnd))&limit=100" -TimeoutSec 3
    $metrics = Invoke-RestMethod -Uri "http://127.0.0.1:19090/api/v1/query?query=opspilot_http_requests_total" -TimeoutSec 3
    $traces = Invoke-RestMethod -Uri "http://127.0.0.1:16686/api/traces?service=orders&limit=100" -TimeoutSec 3
    if ($logs.count -lt 1 -or $deployments.count -lt 1 -or $metrics.status -ne "success" -or $traces.data.Count -lt 1) {
        throw "Telemetry was incomplete: $($case.case_id)"
    }
    if ($case.fault -eq "db_pool_exhaustion" -and -not ($logs.records | Where-Object { $_.message -eq "database sessions remain occupied" })) {
        throw "Connection pressure signal was missing: $($case.case_id)"
    }
    if ($case.fault -eq "retry_storm" -and @($logs.records | Where-Object { $_.message -eq "payment attempt failed" }).Count -lt 3) {
        throw "Retry amplification was not reproduced: $($case.case_id)"
    }
    $expectedVersion = if ($case.fault -eq "bad_release") { "v2" } else { "v1" }
    $latestOrdersDeployment = $deployments.records | Where-Object { $_.service -eq "orders" } | Sort-Object timestamp | Select-Object -Last 1
    if (-not $latestOrdersDeployment -or $latestOrdersDeployment.version -ne $expectedVersion) {
        throw "Expected orders $expectedVersion deployment observation: $($case.case_id)"
    }
    $observed += [pscustomobject]@{ case_id = $case.case_id; status = $status; order_log_records = $logs.count; deployment_records = $deployments.count; trace_count = $traces.data.Count; prometheus_series = $metrics.data.result.Count }
}

$observed | ConvertTo-Json -Depth 4
