param(
    [ValidateRange(10, 180)]
    [int] $ReadyTimeoutSeconds = 90,
    [ValidateRange(1, 60)]
    [int] $EvidenceTimeoutSeconds = 20,
    [ValidateRange(10, 120)]
    [int] $McpTimeoutSeconds = 45,
    [switch] $VerifyMcp
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$telemetryEvidenceModule = Join-Path $PSScriptRoot "telemetry-evidence.psm1"
Import-Module $telemetryEvidenceModule -Force
$composeFile = Join-Path $PSScriptRoot "compose.yaml"
$reportDirectory = Join-Path $root "reports"
$runId = [guid]::NewGuid().ToString()
$startedAtUtc = [DateTime]::UtcNow.ToString("o")
$gitSha = (& git -C $root rev-parse HEAD 2>$null | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $gitSha -notmatch '^[0-9a-fA-F]{40}$') { throw "Could not identify the OpsPilot source revision." }
$orderId = "bad-release-$runId"
$projectName = "opspilot-bad-release-$($runId.Replace('-', '').Substring(0, 12))".ToLowerInvariant()
$ports = @(18080, 18081, 18082, 18083, 14318, 16686, 19090)
$savedEnvironment = @{}
$originalLocation = Get-Location
foreach ($name in @("COMPOSE_PROJECT_NAME", "LAB_FAULT", "LAB_GATEWAY_VERSION", "LAB_ORDERS_VERSION", "LAB_INVENTORY_URL")) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

$result = [ordered]@{
    schema_version = 1
    artifact_type = $(if ($VerifyMcp) { "op2_mcp_bad_release_host_verification" } else { "op4_bad_release_host_verification" })
    status = "running"
    run_id = $runId
    git_sha = $gitSha.ToLowerInvariant()
    started_at_utc = $startedAtUtc
    ended_at_utc = $null
    compose_project = $projectName
    order_id = $orderId
    synthetic_only = $true
    request_attempts = 0
    expected_http_status = 502
    actual_http_status = $null
    window_start_utc = $null
    window_end_utc = $null
    deployment = $null
    matching_order_logs = @()
    cleanup = @{ attempted = $false; containers_empty = $false; volumes_deleted = $false }
}
if ($VerifyMcp) { $result.mcp_verification = $null }
$composeAttempted = $false
$failure = $null

function Invoke-Compose([string[]] $Arguments) {
    & docker compose --project-name $projectName -f $composeFile @Arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed (exit $LASTEXITCODE) for project $projectName" }
}

try {
    if ($env:OPSPILOT_COMPOSE_OVERRIDE) {
        throw "Unset OPSPILOT_COMPOSE_OVERRIDE before running this isolated validation."
    }
    foreach ($port in $ports) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
        try { $listener.Start() }
        catch { throw "Required loopback port $port is occupied; no containers were started." }
        finally { $listener.Stop() }
    }

    Set-Location $root
    $env:COMPOSE_PROJECT_NAME = $projectName
    $env:LAB_FAULT = "bad_release"
    $env:LAB_GATEWAY_VERSION = "v1"
    $env:LAB_ORDERS_VERSION = "v2"
    $env:LAB_INVENTORY_URL = "http://inventory-v2:8080"
    Invoke-Compose @("config", "--quiet")

    $composeAttempted = $true
    & docker compose --project-name $projectName -f $composeFile up --build -d
    if ($LASTEXITCODE -ne 0) { throw "Compose startup failed for isolated project $projectName" }

    $ready = $false
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            Invoke-RestMethod "http://127.0.0.1:18080/health" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod "http://127.0.0.1:18081/health" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod "http://127.0.0.1:18082/health" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod "http://127.0.0.1:18083/ready" -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Lab services did not become ready within $ReadyTimeoutSeconds seconds." }

    $result.window_start_utc = [DateTime]::UtcNow.ToString("o")
    $deployment = [ordered]@{
        record_id = [guid]::NewGuid().ToString()
        run_id = $runId
        order_id = $orderId
        timestamp = $result.window_start_utc
        service = "orders"
        version = "v2"
        synthetic = $true
    } | ConvertTo-Json -Compress
    $deploymentFile = Join-Path $PSScriptRoot "telemetry\deployments.jsonl"
    [IO.Directory]::CreateDirectory((Split-Path -Parent $deploymentFile)) | Out-Null
    [IO.File]::AppendAllText($deploymentFile, $deployment + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

    $result.request_attempts = 1
    try {
        $response = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:18080/checkout" `
            -ContentType "application/json" -Body (@{ order_id = $orderId } | ConvertTo-Json -Compress) -TimeoutSec 15
        $result.actual_http_status = [int]$response.StatusCode
    } catch {
        $httpResponse = $_.Exception.Response
        if (-not $httpResponse -or -not $httpResponse.StatusCode) { throw }
        $result.actual_http_status = [int]$httpResponse.StatusCode
    }
    if ($result.actual_http_status -ne 502) {
        throw "Expected synthetic bad_release HTTP 502, got $($result.actual_http_status)."
    }

    $evidenceDeadline = [DateTime]::UtcNow.AddSeconds($EvidenceTimeoutSeconds)
    $matchingLogs = @()
    $deployments = @()
    while ([DateTime]::UtcNow -lt $evidenceDeadline) {
        $result.window_end_utc = [DateTime]::UtcNow.ToString("o")
        $from = [uri]::EscapeDataString($result.window_start_utc)
        $to = [uri]::EscapeDataString($result.window_end_utc)
        $logResponse = Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$from&end=$to&limit=1000" -TimeoutSec 3
        $deploymentResponse = Invoke-RestMethod "http://127.0.0.1:18080/deployments?start=$from&end=$to&limit=100" -TimeoutSec 3
        $matchingLogs = @($logResponse.records | Where-Object { $_.order_id -eq $orderId })
        $deployments = @($deploymentResponse.records | Where-Object { $_.run_id -eq $runId -and $_.order_id -eq $orderId })
        if ($matchingLogs.Count -gt 0 -and $deployments.Count -eq 1) { break }
        Start-Sleep -Milliseconds 500
    }
    $result.matching_order_logs = $matchingLogs
    if ($deployments.Count -eq 1) { $result.deployment = $deployments[0] }
    if ($matchingLogs.Count -lt 1) { throw "No logs found for unique order $orderId in the reported UTC window." }
    if ($deployments.Count -ne 1 -or $result.deployment.version -ne "v2") {
        throw "Expected exactly one orders/v2 deployment record for run $runId in the reported UTC window."
    }
    $dnsEvidence = @($matchingLogs | Where-Object { Test-InventoryDnsEvidence -Record $_ })
    if ($dnsEvidence.Count -eq 0) {
        throw "Logs did not prove the inventory-v2 DNS failure for order $orderId."
    }

    if ($VerifyMcp) {
        $result.window_end_utc = [DateTime]::UtcNow.ToString("o")
        $python = Join-Path $root '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { throw 'Run .\scripts\setup.ps1 first.' }
        $mcpOutput = & $python -m lab.verify_bad_release_mcp `
            --start $result.window_start_utc --end $result.window_end_utc `
            --order-id $orderId --run-id $runId --timeout-seconds $McpTimeoutSeconds
        if ($LASTEXITCODE -ne 0) { throw "MCP host verification failed for order $orderId." }
        $result.mcp_verification = ($mcpOutput -join [Environment]::NewLine) | ConvertFrom-Json
    }
    $result.status = "passed"
} catch {
    $failure = $_
    $result.status = "failed"
    $result.error = $_.Exception.Message
} finally {
    if ($composeAttempted) {
        $result.cleanup.attempted = $true
        try {
            $existing = @(& docker compose --project-name $projectName -f $composeFile ps --all --quiet)
            if ($LASTEXITCODE -ne 0) { throw "Could not inspect isolated project $projectName for partial startup." }
            if ($existing.Count -gt 0) { Invoke-Compose @("down") }
            $remaining = @(& docker compose --project-name $projectName -f $composeFile ps --all --quiet)
            if ($LASTEXITCODE -ne 0) { throw "Could not verify containers for project $projectName were stopped." }
            $result.cleanup.containers_empty = ($remaining.Count -eq 0)
            if (-not $result.cleanup.containers_empty) { throw "Containers remain in isolated project $projectName." }
        } catch {
            $result.status = "failed"
            $result.cleanup.error = $_.Exception.Message
            if (-not $failure) { $failure = $_ }
        }
    } else {
        $result.cleanup.note = "Compose did not report successful startup; no down command was sent. Check only project $projectName if startup was partial."
    }
    $result.cleanup.volumes_deleted = $false
    $result.ended_at_utc = [DateTime]::UtcNow.ToString("o")
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    Set-Location $originalLocation
    [IO.Directory]::CreateDirectory($reportDirectory) | Out-Null
    $reportPrefix = if ($VerifyMcp) { "op2-mcp-bad-release-host" } else { "op4-bad-release-host" }
    $reportPath = Join-Path $reportDirectory "$reportPrefix-$runId.json"
    $result.report = "reports/$reportPrefix-$runId.json"
    $result.run_manifest = "reports/$reportPrefix-$runId.run-manifest.json"
    [IO.File]::WriteAllText($reportPath, (($result | ConvertTo-Json -Depth 8) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    $manifestPython = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $manifestPython)) { $manifestPython = (Get-Command python -ErrorAction SilentlyContinue).Source }
    if (-not $manifestPython) {
        $failure = [InvalidOperationException]::new('Python is required to write the shared run manifest.')
        $result.status = "failed"
        $result.manifest_error = $failure.Message
        [IO.File]::WriteAllText($reportPath, (($result | ConvertTo-Json -Depth 8) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    } else {
        & $manifestPython (Join-Path $root 'scripts\write_host_verification_manifest.py') --report-path $result.report
        if ($LASTEXITCODE -ne 0) {
            $failure = [InvalidOperationException]::new('Shared run manifest generation failed; host report is preserved.')
            $result.status = "failed"
            $result.manifest_error = $failure.Message
            [IO.File]::WriteAllText($reportPath, (($result | ConvertTo-Json -Depth 8) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
        }
    }
}

$result | ConvertTo-Json -Depth 8
if ($failure) { throw $failure }
