param(
    [Parameter(Mandatory = $true)] [string] $CaseFile,
    [Parameter(Mandatory = $true)] [string] $OutputFile,
    [Parameter(Mandatory = $true)] [string] $SnapshotFile
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "compose.yaml"
$case = Get-Content -LiteralPath $CaseFile -Raw | ConvertFrom-Json
$fault = [string]$case.injection.fault
if ($case.injection.service -ne "orders" -or $fault -notin @("dependency_timeout", "db_pool_exhaustion")) {
    throw "Only reviewed orders dependency_timeout and db_pool_exhaustion cases are allowed."
}
$images = @(
    "opspilot-lab-gateway:latest", "opspilot-lab-orders:latest", "opspilot-lab-inventory:latest", "opspilot-lab-payment:latest",
    "postgres:16-alpine", "redis:7-alpine", "otel/opentelemetry-collector-contrib:0.138.0",
    "jaegertracing/all-in-one:1.76.0", "prom/prometheus:v3.7.3"
)
foreach ($image in $images) {
    docker image inspect $image --format "{{.Id}}" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Missing local image; refusing build or pull: $image" }
}

$env:LAB_FAULT = "none"
$env:LAB_ORDERS_VERSION = "v1"
$env:LAB_INVENTORY_URL = "http://inventory:8080"
docker compose -f $composeFile config --quiet
if ($LASTEXITCODE -ne 0) { throw "Invalid compose configuration." }
docker compose -f $composeFile up --no-build --force-recreate -d
if ($LASTEXITCODE -ne 0) { throw "Could not start local services from existing images." }

function Wait-Ready([int]$Attempts = 60) {
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:18081/health" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod -Uri "http://127.0.0.1:18083/ready" -TimeoutSec 2 | Out-Null
            return
        } catch { Start-Sleep -Seconds 1 }
    }
    throw "Orders/payment did not become ready."
}

Wait-Ready
docker compose -f $composeFile exec -T postgres psql -U opspilot -d opspilot -c "UPDATE inventory SET available=20 WHERE sku='demo-1'; DELETE FROM orders;"
if ($LASTEXITCODE -ne 0) { throw "Could not reset local database." }

$baselineId = [guid]::NewGuid().ToString()
$faultId = [guid]::NewGuid().ToString()
$recoveryId = [guid]::NewGuid().ToString()
$assessmentStart = [DateTime]::UtcNow.ToString("o")
$baselineWatch = [Diagnostics.Stopwatch]::StartNew()
$baselineStatus = $null
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$baselineId}|ConvertTo-Json) -TimeoutSec 15 | Out-Null
    $baselineStatus = 200
} catch { if ($_.Exception.Response) {$baselineStatus=[int]$_.Exception.Response.StatusCode} }
$baselineWatch.Stop()
if ($baselineStatus -ne 200) { throw "Healthy baseline failed; injection skipped." }
Start-Sleep -Seconds 4

$env:LAB_FAULT = $fault
docker compose -f $composeFile up --no-build --force-recreate -d orders payment
if ($LASTEXITCODE -ne 0) { throw "Could not enable reviewed fault." }
Wait-Ready
$faultStart = [DateTime]::UtcNow.ToString("o")
$deployment = @{record_id=[guid]::NewGuid().ToString();timestamp=$faultStart;service="orders";version="v1"}|ConvertTo-Json -Compress
[IO.File]::AppendAllText((Join-Path $PSScriptRoot "telemetry/deployments.jsonl"),$deployment+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
$faultWatch = [Diagnostics.Stopwatch]::StartNew()
$faultStatus = $null
$faultError = $null
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$faultId}|ConvertTo-Json) -TimeoutSec 15 | Out-Null
    $faultStatus = 200
} catch {
    $faultError = $_.Exception.GetType().Name
    if ($_.Exception.Response) {$faultStatus=[int]$_.Exception.Response.StatusCode}
}
$faultWatch.Stop()
$faultCompleted = [DateTime]::UtcNow.ToString("o")
Start-Sleep -Seconds 4
$windowEnd = [DateTime]::UtcNow.ToString("o")
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Expected project Python environment is missing: $python" }
& $python (Join-Path $repoRoot "scripts/capture_lab_snapshot.py") --start $assessmentStart --end $windowEnd --output $SnapshotFile
if ($LASTEXITCODE -ne 0) { throw "Could not freeze truth-free telemetry before service recovery." }

$env:LAB_FAULT = "none"
docker compose -f $composeFile up --no-build --force-recreate -d orders payment
if ($LASTEXITCODE -ne 0) { throw "Could not disable fault for recovery." }
Wait-Ready
$recoveryWatch = [Diagnostics.Stopwatch]::StartNew()
$recoveryStatus = $null
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$recoveryId}|ConvertTo-Json) -TimeoutSec 15 | Out-Null
    $recoveryStatus = 200
} catch { if ($_.Exception.Response) {$recoveryStatus=[int]$_.Exception.Response.StatusCode} }
$recoveryWatch.Stop()

$result = @{
    case_id=$case.case_id; scenario=$fault; trial_id=[guid]::NewGuid().ToString()
    timeline=@{
        assessment_start=$assessmentStart; assessment_window=@{start=$assessmentStart;end=$windowEnd}
        baseline_order_id=$baselineId; baseline_response_status=$baselineStatus; baseline_request_duration_ms=[math]::Round($baselineWatch.Elapsed.TotalMilliseconds,1)
        fault_window_start=$faultStart; fault_window_end=$windowEnd; fault_order_id=$faultId; fault_response_status=$faultStatus
        fault_response_error_type=$faultError; fault_request_duration_ms=[math]::Round($faultWatch.Elapsed.TotalMilliseconds,1); fault_request_completed=$faultCompleted
        recovery_order_id=$recoveryId; recovery_response_status=$recoveryStatus; recovery_request_duration_ms=[math]::Round($recoveryWatch.Elapsed.TotalMilliseconds,1)
    }
}
$parent = Split-Path -Parent $OutputFile
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputFile -Encoding utf8
Write-Host "scenario=$fault baseline=$baselineStatus/$([math]::Round($baselineWatch.Elapsed.TotalMilliseconds,1))ms fault=$faultStatus/$([math]::Round($faultWatch.Elapsed.TotalMilliseconds,1))ms recovery=$recoveryStatus/$([math]::Round($recoveryWatch.Elapsed.TotalMilliseconds,1))ms"
if ($null -eq $faultStatus -or $recoveryStatus -ne 200 -or $faultWatch.Elapsed.TotalMilliseconds -le $baselineWatch.Elapsed.TotalMilliseconds -or $faultWatch.Elapsed.TotalMilliseconds -le $recoveryWatch.Elapsed.TotalMilliseconds) {
    throw "Fault did not create a measurable degradation followed by recovery."
}
