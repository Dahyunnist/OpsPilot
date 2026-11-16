param(
    [string] $OutputFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "reports/op1-slow-sql-isolated.json")
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "compose.yaml"
$composeOverride = Join-Path $PSScriptRoot "compose.slow-sql.yaml"
$composeArgs = @("-f", $composeFile, "-f", $composeOverride)
$runner = Join-Path $PSScriptRoot "run-vertical-slice.ps1"
$serviceDirectory = Join-Path $PSScriptRoot "service"
$overlayDockerfile = Join-Path $serviceDirectory "Dockerfile.slow-sql"
$isolatedImage = "opspilot-lab-orders-slow-sql:latest"
$resolvedOutput = [IO.Path]::GetFullPath($OutputFile)
$outputDirectory = Split-Path -Parent $resolvedOutput
$projectName = "opspilot-slow-sql-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$priorProject = $env:COMPOSE_PROJECT_NAME
$priorComposeOverride = $env:OPSPILOT_COMPOSE_OVERRIDE
$priorFault = $env:LAB_FAULT
$priorVersion = $env:LAB_ORDERS_VERSION
$priorInventory = $env:LAB_INVENTORY_URL
$runAttempted = $false
$cleanupVerified = $false
$result = $null
$evidencePassed = $false
$evidenceError = $null

try {
    if (Test-Path -LiteralPath $resolvedOutput) { throw "Refusing to overwrite existing report: $resolvedOutput" }
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    foreach ($port in @(18080, 18081, 18082, 18083, 14318, 16686, 19090)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
            throw "Required lab port $port is already in use; isolated run was not started."
        }
    }
    foreach ($image in @(
        "opspilot-lab-gateway:latest", "opspilot-lab-orders:latest",
        "opspilot-lab-inventory:latest", "opspilot-lab-payment:latest",
        "postgres:16-alpine", "redis:7-alpine",
        "otel/opentelemetry-collector-contrib:0.138.0",
        "jaegertracing/all-in-one:1.76.0", "prom/prometheus:v3.7.3"
    )) {
        docker image inspect $image --format "{{.Id}}" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Required local image is missing; refusing build or pull: $image" }
    }

    docker build --pull=false --network=none -f $overlayDockerfile -t $isolatedImage $serviceDirectory
    if ($LASTEXITCODE -ne 0) { throw "Could not build the isolated orders image from local files and images." }

    $env:COMPOSE_PROJECT_NAME = $projectName
    $env:OPSPILOT_COMPOSE_OVERRIDE = $composeOverride
    $runAttempted = $true
    & $runner -OutputFile $resolvedOutput
    if ($LASTEXITCODE -ne 0) { throw "Isolated slow_sql run failed." }

    $result = Get-Content -LiteralPath $resolvedOutput -Raw | ConvertFrom-Json
    $faultTelemetry = $result.fault_telemetry
    $orderId = $faultTelemetry.order_id
    $orderLogs = @($faultTelemetry.logs | Where-Object { $_.order_id -eq $orderId -and $_.trace_id })
    $traceIds = @($faultTelemetry.trace_ids)
    $correlated = @($faultTelemetry.traces | Where-Object {
        $_.operation -eq "postgres.query" -and [long]$_.duration_us -ge 1200000 -and $_.trace_id -in $traceIds
    })
    $evidencePassed = ($faultTelemetry.correlated -and $orderLogs.Count -gt 0 -and $correlated.Count -gt 0)
    $evidenceError = $faultTelemetry.error
    if (-not $evidencePassed -and -not $evidenceError) { $evidenceError = "Fault telemetry lacks a linked slow postgres.query span." }
    $result | Add-Member -NotePropertyName evidence -NotePropertyValue @{
        query_window = $faultTelemetry.query_window
        order_id = $orderId
        logs = @($orderLogs | Select-Object timestamp, service, message, order_id, trace_id, span_id)
        traces = @($faultTelemetry.traces)
        trace_ids_queried = $traceIds
        correlated = $evidencePassed
        failure_stage = $faultTelemetry.failure_stage
        error = $evidenceError
        diagnostics = @{
            jaeger_query = $faultTelemetry.jaeger_query
            raw_jaeger_response = $faultTelemetry.raw_jaeger_response
            collector_status = $faultTelemetry.collector_status
        }
    } -Force
    $result | Add-Member -NotePropertyName compose_project -NotePropertyValue $projectName -Force
    $timeline = $result.timeline
    $requestSucceeded = ($timeline.fault_response_status -eq 200 -and [double]$timeline.fault_request_duration_ms -ge 1200)
    $recoverySucceeded = ($timeline.recovery_response_status -eq 200 -and [double]$timeline.recovery_request_duration_ms -lt [double]$timeline.fault_request_duration_ms)
    $result | Add-Member -NotePropertyName acceptance -NotePropertyValue ([ordered]@{
        slow_request_succeeded = $requestSucceeded
        recovery_succeeded = $recoverySucceeded
        telemetry_evidence_complete = $evidencePassed
        status = if (-not $requestSucceeded) { "slow_request_failed" } elseif (-not $recoverySucceeded) { "recovery_failed" } elseif (-not $evidencePassed) { "telemetry_evidence_missing" } else { "passed" }
    }) -Force
} finally {
    try {
        if ($runAttempted) {
            docker compose --project-name $projectName @composeArgs down
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Could not stop isolated Compose project $projectName."
            } else {
                $remaining = docker compose --project-name $projectName @composeArgs ps --all --quiet
                $cleanupVerified = ($LASTEXITCODE -eq 0 -and @($remaining).Count -eq 0)
                if (-not $cleanupVerified) {
                    Write-Warning "Isolated Compose project $projectName still has containers."
                }
            }
        }
    } finally {
        if ($null -eq $priorProject) { Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue } else { $env:COMPOSE_PROJECT_NAME = $priorProject }
        if ($null -eq $priorComposeOverride) { Remove-Item Env:OPSPILOT_COMPOSE_OVERRIDE -ErrorAction SilentlyContinue } else { $env:OPSPILOT_COMPOSE_OVERRIDE = $priorComposeOverride }
        if ($null -eq $priorFault) { Remove-Item Env:LAB_FAULT -ErrorAction SilentlyContinue } else { $env:LAB_FAULT = $priorFault }
        if ($null -eq $priorVersion) { Remove-Item Env:LAB_ORDERS_VERSION -ErrorAction SilentlyContinue } else { $env:LAB_ORDERS_VERSION = $priorVersion }
        if ($null -eq $priorInventory) { Remove-Item Env:LAB_INVENTORY_URL -ErrorAction SilentlyContinue } else { $env:LAB_INVENTORY_URL = $priorInventory }
    }
}

$result | Add-Member -NotePropertyName cleanup -NotePropertyValue @{
    containers_empty = $cleanupVerified
    volumes_deleted = $false
} -Force
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $resolvedOutput -Encoding utf8
Write-Host "Isolated slow_sql evidence recorded: $resolvedOutput"
Write-Host "Acceptance status=$($result.acceptance.status); slow_request=$($result.acceptance.slow_request_succeeded); recovery=$($result.acceptance.recovery_succeeded); telemetry=$($result.acceptance.telemetry_evidence_complete)"
if (-not $cleanupVerified) { throw "Isolated Compose cleanup was not verified; volumes were not deleted." }
if (-not $evidencePassed) { throw "Slow SQL request and recovery were recorded separately; telemetry evidence missing: $evidenceError" }
if ($result.acceptance.status -ne "passed") { throw "Isolated slow_sql acceptance failed: $($result.acceptance.status)" }
