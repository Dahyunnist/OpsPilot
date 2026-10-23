param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("db_pool_exhaustion", "dependency_timeout", "bad_release", "slow_sql", "retry_storm", "double_root")]
    [string] $Scenario
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "compose.yaml"
$env:LAB_FAULT = $Scenario
$env:LAB_ORDERS_VERSION = if ($Scenario -eq "bad_release") { "v2" } else { "v1" }
$env:LAB_INVENTORY_URL = if ($Scenario -eq "bad_release") { "http://inventory-v2:8080" } else { "http://inventory:8080" }
$telemetryPath = Join-Path $PSScriptRoot "telemetry"
New-Item -ItemType Directory -Path $telemetryPath -Force | Out-Null

docker compose -f $composeFile up --build --force-recreate -d
if ($LASTEXITCODE -ne 0) { throw "Compose failed to start the lab." }

docker compose -f $composeFile exec -T postgres psql -U opspilot -d opspilot -c "UPDATE inventory SET available=20 WHERE sku='demo-1'; DELETE FROM orders;"
if ($LASTEXITCODE -ne 0) { throw "Could not reset deterministic lab state." }

$deployment = @{
    record_id = [guid]::NewGuid().ToString()
    timestamp = [DateTime]::UtcNow.ToString("o")
    service = "orders"
    version = $env:LAB_ORDERS_VERSION
} | ConvertTo-Json -Compress
$deploymentPath = Join-Path $telemetryPath "deployments.jsonl"
[System.IO.File]::AppendAllText($deploymentPath, $deployment + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))

Write-Host "Scenario ready: $Scenario"
Write-Host "Gateway: http://127.0.0.1:18080"
Write-Host "Send POST /checkout with JSON { order_id: <unique-id> } to trigger one request."
Write-Host "Read-only views: Prometheus http://127.0.0.1:19090 and Jaeger http://127.0.0.1:16686."
Write-Host "Bounded evidence: /logs and /deployments require start, end (UTC ISO 8601), and limit=1..1000."
