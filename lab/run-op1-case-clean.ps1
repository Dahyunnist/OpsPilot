param(
    [Parameter(Mandatory = $true)] [string] $CaseFile,
    [Parameter(Mandatory = $true)] [string] $OutputFile,
    [Parameter(Mandatory = $true)] [string] $SnapshotFile
)

$ErrorActionPreference = "Stop"
$runExit = 0
$failure = $null
try {
    & (Join-Path $PSScriptRoot "run-op1-case.ps1") -CaseFile $CaseFile -OutputFile $OutputFile -SnapshotFile $SnapshotFile
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { $runExit = $LASTEXITCODE; throw "OP-1 runner exited with $runExit" }
} catch {
    if ($runExit -eq 0) { $runExit = 1 }
    $failure = $_
} finally {
    & (Join-Path $PSScriptRoot "stop.ps1")
    $stopExit = $LASTEXITCODE
    docker compose -f (Join-Path $PSScriptRoot "compose.yaml") ps --all
    $psExit = $LASTEXITCODE
    if ($stopExit -ne 0 -or $psExit -ne 0) { $runExit = 1 }
}

if ($failure) { Write-Error $failure }
exit $runExit
