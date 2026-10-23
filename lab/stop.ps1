$ErrorActionPreference = "Stop"
docker compose -f (Join-Path $PSScriptRoot "compose.yaml") down
if ($LASTEXITCODE -ne 0) { throw "Compose failed to stop the lab." }
