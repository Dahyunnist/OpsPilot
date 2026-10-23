param(
    [string] $CaseFile = (Join-Path $PSScriptRoot "scenarios/vertical-slow-sql.json"),
    [string] $OutputFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "reports/vertical-slow-sql-window.json")
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "compose.yaml"
$case = Get-Content -LiteralPath $CaseFile -Raw | ConvertFrom-Json
if ($case.injection.fault -ne "slow_sql" -or $case.injection.service -ne "orders") {
    throw "This vertical slice only permits the reviewed slow_sql injection on orders."
}
$requiredImages = @(
    "opspilot-lab-gateway:latest", "opspilot-lab-orders:latest", "opspilot-lab-inventory:latest", "opspilot-lab-payment:latest",
    "postgres:16-alpine", "redis:7-alpine", "otel/opentelemetry-collector-contrib:0.138.0",
    "jaegertracing/all-in-one:1.76.0", "prom/prometheus:v3.7.3"
)
foreach ($image in $requiredImages) {
    docker image inspect $image --format "{{.Id}}" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Required local image is missing; refusing build or pull: $image" }
}

$env:LAB_FAULT = "none"
$env:LAB_ORDERS_VERSION = "v1"
$env:LAB_INVENTORY_URL = "http://inventory:8080"
docker compose -f $composeFile config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose configuration is invalid." }
docker compose -f $composeFile up --no-build --force-recreate -d
if ($LASTEXITCODE -ne 0) { throw "Local images could not start the fault lab." }

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:18081/health" -TimeoutSec 2 | Out-Null
        Invoke-RestMethod -Uri "http://127.0.0.1:18082/health" -TimeoutSec 2 | Out-Null
        Invoke-RestMethod -Uri "http://127.0.0.1:18083/ready" -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw "Service dependencies did not become ready." }

docker compose -f $composeFile exec -T postgres psql -U opspilot -d opspilot -c "UPDATE inventory SET available=20 WHERE sku='demo-1'; DELETE FROM orders;"
if ($LASTEXITCODE -ne 0) { throw "Local lab database reset failed." }

$trialId = [guid]::NewGuid().ToString()
$baselineOrder = [guid]::NewGuid().ToString()
$faultOrder = [guid]::NewGuid().ToString()
$recoveryOrder = [guid]::NewGuid().ToString()
$assessmentStart = [DateTime]::UtcNow.ToString("o")
$baselineWatch = [System.Diagnostics.Stopwatch]::StartNew()
$baselineStatus = $null
$baselineError = $null
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" `
        -Body (@{ order_id = $baselineOrder } | ConvertTo-Json) -TimeoutSec 10 | Out-Null
    $baselineStatus = 200
} catch {
    $baselineError = $_.Exception.GetType().Name
    if ($_.Exception.Response) { $baselineStatus = [int]$_.Exception.Response.StatusCode }
}
$baselineWatch.Stop()
$baselineCompleted = [DateTime]::UtcNow.ToString("o")
if ($baselineStatus -ne 200) { throw "Healthy baseline checkout failed; fault injection was skipped." }
Start-Sleep -Seconds 4

# Inject only after a healthy baseline has been measured and scraped.
$env:LAB_FAULT = "slow_sql"
docker compose -f $composeFile up --no-build --force-recreate -d orders payment
if ($LASTEXITCODE -ne 0) { throw "Could not start the reviewed slow_sql injection." }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:18081/health" -TimeoutSec 2 | Out-Null
        Invoke-RestMethod -Uri "http://127.0.0.1:18083/ready" -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw "Service dependencies did not become ready for fault injection." }

$windowStart = [DateTime]::UtcNow.ToString("o")
$deployment = @{ record_id = [guid]::NewGuid().ToString(); timestamp = $windowStart; service = "orders"; version = "v1" } | ConvertTo-Json -Compress
$deploymentPath = Join-Path $PSScriptRoot "telemetry/deployments.jsonl"
[System.IO.File]::AppendAllText($deploymentPath, $deployment + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))

$faultWatch = [System.Diagnostics.Stopwatch]::StartNew()
$faultStatus = $null
$faultError = $null
$faultRequestCompleted = $null
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" `
        -Body (@{ order_id = $faultOrder } | ConvertTo-Json) -TimeoutSec 10 | Out-Null
    $faultStatus = 200
} catch {
    $faultError = $_.Exception.GetType().Name
    if ($_.Exception.Response) { $faultStatus = [int]$_.Exception.Response.StatusCode }
}
$faultWatch.Stop()
$faultRequestCompleted = [DateTime]::UtcNow.ToString("o")
Start-Sleep -Seconds 4
$windowEnd = [DateTime]::UtcNow.ToString("o")

# Recovery: recreate only fault-bearing services with the injection disabled, then replay a fresh order.
$env:LAB_FAULT = "none"
docker compose -f $composeFile up --no-build --force-recreate -d orders payment
if ($LASTEXITCODE -ne 0) { throw "Could not recreate services without the injected fault." }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:18081/health" -TimeoutSec 2 | Out-Null
        Invoke-RestMethod -Uri "http://127.0.0.1:18083/ready" -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw "Services did not recover after removing the injected fault." }

$recoveryStarted = [DateTime]::UtcNow.ToString("o")
$recoveryWatch = [System.Diagnostics.Stopwatch]::StartNew()
$recoveryStatus = $null
$recoveryError = $null
$recoveryRequestCompleted = $null
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" `
        -Body (@{ order_id = $recoveryOrder } | ConvertTo-Json) -TimeoutSec 10 | Out-Null
    $recoveryStatus = 200
} catch {
    $recoveryError = $_.Exception.GetType().Name
    if ($_.Exception.Response) { $recoveryStatus = [int]$_.Exception.Response.StatusCode }
}
$recoveryWatch.Stop()
$recoveryRequestCompleted = [DateTime]::UtcNow.ToString("o")

$result = @{
    case_id = $case.case_id
    trial_id = $trialId
    scenario = "slow_sql"
    timeline = @{
        assessment_start = $assessmentStart
        assessment_window = @{ start = $assessmentStart; end = $windowEnd }
        baseline_order_id = $baselineOrder
        baseline_response_status = $baselineStatus
        baseline_response_error_type = $baselineError
        baseline_request_duration_ms = [math]::Round($baselineWatch.Elapsed.TotalMilliseconds, 1)
        baseline_completed = $baselineCompleted
        fault_window_start = $windowStart
        fault_window_end = $windowEnd
        fault_request_order_id = $faultOrder
        fault_response_status = $faultStatus
        fault_response_error_type = $faultError
        fault_request_duration_ms = [math]::Round($faultWatch.Elapsed.TotalMilliseconds, 1)
        fault_request_completed = $faultRequestCompleted
        recovery_started = $recoveryStarted
        recovery_request_order_id = $recoveryOrder
        recovery_response_status = $recoveryStatus
        recovery_response_error_type = $recoveryError
        recovery_request_duration_ms = [math]::Round($recoveryWatch.Elapsed.TotalMilliseconds, 1)
        recovery_request_completed = $recoveryRequestCompleted
    }
}
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputFile -Encoding utf8
Write-Host "Fault window and recovery recorded: $OutputFile"
Write-Host "Fault status=$faultStatus duration_ms=$([math]::Round($faultWatch.Elapsed.TotalMilliseconds, 1)); recovery status=$recoveryStatus duration_ms=$([math]::Round($recoveryWatch.Elapsed.TotalMilliseconds, 1))"
if ($faultStatus -ne 200 -or $faultWatch.Elapsed.TotalMilliseconds -lt 1200) {
    throw "The injected slow query did not produce the expected successful, delayed checkout."
}
if ($baselineStatus -ne 200 -or $baselineWatch.Elapsed.TotalMilliseconds -ge $faultWatch.Elapsed.TotalMilliseconds) {
    throw "Healthy baseline did not complete successfully before the injected delay."
}
if ($recoveryStatus -ne 200 -or $recoveryWatch.Elapsed.TotalMilliseconds -ge $faultWatch.Elapsed.TotalMilliseconds) {
    throw "Checkout did not return faster after the fault was disabled."
}
