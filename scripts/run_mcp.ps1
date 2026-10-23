param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('logs', 'traces', 'live')]
    [string] $Source
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Run .\scripts\setup.ps1 first.'
    }
    & $python -m opspilot.mcp_servers $Source
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
