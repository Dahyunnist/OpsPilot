param(
    [Parameter(Mandatory = $true)]
    [string] $IncidentId,
    [int] $QueryBudget = 8,
    [string[]] $AdditionalArgs = @()
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Run .\scripts\setup.ps1 first.'
    }
    & $python -m opspilot investigate $IncidentId --query-budget $QueryBudget @AdditionalArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
