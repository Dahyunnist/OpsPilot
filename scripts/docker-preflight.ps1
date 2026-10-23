[CmdletBinding()]
param(
    [switch] $RunSmoke
)

$ErrorActionPreference = "Stop"
$global:LASTEXITCODE = 0
$root = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $root "lab/compose.yaml"
$smokeScript = Join-Path $root "lab/smoke.ps1"
$docker = Get-Command docker -ErrorAction SilentlyContinue

if (-not $docker) {
    Write-Host "Docker CLI: missing"
    Write-Host "Install or repair Docker Desktop, then rerun this preflight. No service or model was started."
    $global:LASTEXITCODE = 2
    return
}

$composeOutput = @(& $docker.Source compose -f $composeFile config --quiet 2>&1)
$composeExit = $LASTEXITCODE
if ($composeExit -ne 0) {
    Write-Host "Compose configuration: invalid"
    $composeOutput | ForEach-Object { Write-Host $_ }
    Write-Host "Fix the Compose error above. No containers or model requests were started."
    $global:LASTEXITCODE = 2
    return
}
Write-Host "Compose configuration: valid"

$service = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
$desktop = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue | Select-Object -First 1
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$engineOutput = @(& $docker.Source info --format '{{.ServerVersion}}' 2>&1)
$engineExit = $LASTEXITCODE
if ($engineExit -ne 0) {
    Write-Host "Docker Desktop process: $(if ($desktop) { 'running' } else { 'not running' })"
    Write-Host "Docker Desktop service: $(if ($service) { $service.Status } else { 'not installed' })"
    Write-Host "Docker Engine: unavailable"
    $engineOutput | ForEach-Object { Write-Host "Engine error: $_" }
    if ($service -and $service.Status -ne "Running") {
        Write-Host "Next step: start Docker Desktop as administrator and wait for its Engine status to become running."
        if ($isAdmin) { Write-Host "Elevated PowerShell alternative: Start-Service -Name com.docker.service" }
    } else {
        Write-Host "Next step: start Docker Desktop, then rerun this preflight."
    }
    Write-Host "No containers, smoke tests, or model requests were started."
    $global:LASTEXITCODE = 2
    return
}

Write-Host "Docker Engine: $($engineOutput -join ' ')"
if (-not $RunSmoke) {
    Write-Host "Ready. Run .\scripts\docker-preflight.ps1 -RunSmoke for six-scenario service smoke."
    return
}

Write-Host "Running six-scenario service smoke; no model API calls are made."
try {
    & $smokeScript
} catch {
    Write-Host "Docker smoke failed: $($_.Exception.Message)"
    $global:LASTEXITCODE = 2
    return
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker smoke failed with exit code $LASTEXITCODE."
    $global:LASTEXITCODE = 2
    return
}
Write-Host "Docker smoke: passed"
