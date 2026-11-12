$ErrorActionPreference = "Stop"
$composeFiles = @("-f", (Join-Path $PSScriptRoot "compose.yaml"))
if ($env:OPSPILOT_COMPOSE_OVERRIDE) { $composeFiles += @("-f", $env:OPSPILOT_COMPOSE_OVERRIDE) }
docker compose @composeFiles down
if ($LASTEXITCODE -ne 0) { throw "Compose failed to stop the lab." }
