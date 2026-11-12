$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "compose.yaml"
$output = Join-Path (Split-Path -Parent $PSScriptRoot) "reports/op1-retry-storm-host.json"
$exitCode = 0
try {
    & (Join-Path $PSScriptRoot "run-scenario.ps1") -Scenario retry_storm
    if ($LASTEXITCODE -ne 0) { throw "retry_storm startup failed" }
    $id = "retry-storm-" + [guid]::NewGuid().ToString()
    $start = [DateTime]::UtcNow.ToString("o")
    $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$id}|ConvertTo-Json) -TimeoutSec 12
    $end = [DateTime]::UtcNow.ToString("o")
    $logs = Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$([uri]::EscapeDataString($start))&end=$([uri]::EscapeDataString($end))&limit=100"
    $failed = @($logs.records | Where-Object { $_.message -eq "payment attempt failed" })
    if ($failed.Count -lt 3) { throw "retry_storm evidence missing: expected at least 3 failed attempts" }
    @{scenario="retry_storm";order_id=$id;start=$start;end=$end;response=$response;failed_attempts=$failed.Count;live_run=$true} | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $output
} catch { $exitCode=1; Write-Error $_ } finally {
    & (Join-Path $PSScriptRoot "stop.ps1")
    if ($LASTEXITCODE -ne 0) { $exitCode=1 }
    docker compose -f $compose ps --all
    if ($LASTEXITCODE -ne 0) { $exitCode=1 }
}
exit $exitCode
