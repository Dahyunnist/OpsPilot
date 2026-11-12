$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "compose.yaml"
$output = Join-Path (Split-Path -Parent $PSScriptRoot) "reports/op1-slow-sql-host.json"
$exitCode = 0
try {
    & (Join-Path $PSScriptRoot "run-scenario.ps1") -Scenario slow_sql
    if ($LASTEXITCODE -ne 0) { throw "slow_sql startup failed" }
    $id = "slow-sql-" + [guid]::NewGuid().ToString()
    $start = [DateTime]::UtcNow.ToString("o")
    $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$id}|ConvertTo-Json) -TimeoutSec 12
    $end = [DateTime]::UtcNow.ToString("o")
    $proof = docker compose -f $compose logs --no-color orders postgres
    if ($LASTEXITCODE -ne 0 -or ($proof -join "`n") -notmatch "pg_sleep|postgres.query") { throw "slow_sql evidence missing" }
    @{scenario="slow_sql";order_id=$id;start=$start;end=$end;response=$response;log_signal="pg_sleep/postgres.query";live_run=$true} | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $output
} catch { $exitCode=1; Write-Error $_ } finally {
    & (Join-Path $PSScriptRoot "stop.ps1")
    if ($LASTEXITCODE -ne 0) { $exitCode=1 }
    docker compose -f $compose ps --all
    if ($LASTEXITCODE -ne 0) { $exitCode=1 }
}
exit $exitCode


