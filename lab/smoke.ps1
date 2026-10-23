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
            Invoke-RestMethod -Uri "http://127.0.0.1:18080/ready" -TimeoutSec 2 | Out-Null
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
    Start-Sleep -Seconds 3

    $logs = Invoke-RestMethod -Uri "http://127.0.0.1:18081/logs?limit=1000" -TimeoutSec 3
    $deployments = Invoke-RestMethod -Uri "http://127.0.0.1:18080/deployments?limit=100" -TimeoutSec 3
    $metrics = Invoke-RestMethod -Uri "http://127.0.0.1:19090/api/v1/query?query=opspilot_http_requests_total" -TimeoutSec 3
    if ($logs.count -lt 1 -or $deployments.count -lt 1 -or $metrics.status -ne "success") {
        throw "Telemetry was incomplete: $($case.case_id)"
    }
    $observed += [pscustomobject]@{ case_id = $case.case_id; status = $status; order_log_records = $logs.count; deployment_records = $deployments.count; prometheus_series = $metrics.data.result.Count }
}

$observed | ConvertTo-Json -Depth 4
