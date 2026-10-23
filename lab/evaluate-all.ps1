$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$truth = Get-Content -LiteralPath (Join-Path $PSScriptRoot "scenarios/truth.json") -Raw | ConvertFrom-Json
$runScript = Join-Path $PSScriptRoot "run-scenario.ps1"
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Run .\scripts\setup.ps1 first." }
$reportPaths = @()

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
    if (-not $ready) { throw "Service dependencies did not become ready: $($case.case_id)" }

    $start = [DateTime]::UtcNow.AddSeconds(-5).ToString("o")
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{ order_id = $case.case_id } | ConvertTo-Json) -TimeoutSec 8 | Out-Null
    } catch {
        # Failed checkout is expected for timeout, bad-release and pressure scenarios.
    }
    Start-Sleep -Seconds 3
    $end = [DateTime]::UtcNow.ToString("o")
    $reportPath = Join-Path $root "reports/lab-strategy-$($case.case_id).json"
    & $python (Join-Path $root "scripts/evaluate_lab.py") --case-id $case.case_id --start $start --end $end --strategies rules,single,multi,auto --max-cost-usd 0.50 --output $reportPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Strategy evaluation failed: $($case.case_id)" }
    $reportPaths += $reportPath
}

$arguments = @((Join-Path $root "scripts/summarize_lab.py"), "--output", (Join-Path $root "reports/lab-strategy-summary.json"))
foreach ($reportPath in $reportPaths) { $arguments += @("--input", $reportPath) }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Strategy summary failed." }
