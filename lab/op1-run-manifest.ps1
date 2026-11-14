$script:Op1RepositoryRoot = Split-Path -Parent $PSScriptRoot

function Get-Op1ArtifactSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-Op1RunManifestArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$ReportPath,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$TraceId,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$EndedAt,
        [Parameter(Mandatory = $true)][ValidateSet("preflight", "full_matrix")][string]$Phase,
        [Parameter(Mandatory = $true)][ValidateSet("completed", "failed")][string]$Status,
        [Parameter(Mandatory = $true)][int]$CaseCount,
        [Parameter(Mandatory = $true)][int]$PassedCount,
        [Parameter(Mandatory = $true)][int]$FailedCount,
        [Parameter(Mandatory = $true)][bool]$PreflightPassed,
        [Parameter(Mandatory = $true)][bool]$LabStarted,
        $CleanupEmpty
    )

    $gitSha = (& git -C $script:Op1RepositoryRoot rev-parse HEAD 2>$null | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or $gitSha -notmatch '^[0-9a-fA-F]{40}$') {
        throw "Could not identify the OpsPilot source revision for the run manifest."
    }
    $truthPath = Join-Path $PSScriptRoot "scenarios/truth.json"
    $reportSha = Get-Op1ArtifactSha256 $ReportPath
    $truthSha = Get-Op1ArtifactSha256 $truthPath
    $evaluatorSha = Get-Op1ArtifactSha256 (Join-Path $PSScriptRoot "run-host-matrix.ps1")
    $scorerSha = Get-Op1ArtifactSha256 (Join-Path $PSScriptRoot "op1-matrix-contract.ps1")
    $manifest = [ordered]@{
        schema_version = 1
        project = "OpsPilot"
        run_id = $RunId
        evaluation_id = "op1_host_matrix"
        dataset_id = "op1_lab_truth_v1"
        dataset_sha256 = $truthSha
        truth_sha256 = $truthSha
        split = $Phase
        evaluator_sha256 = $evaluatorSha
        result_fingerprint_sha256 = $reportSha
        git_sha = $gitSha.ToLowerInvariant()
        model_id = "none"
        prompt_sha = $null
        tool_schema_sha = $null
        runtime_digest = $null
        max_steps = 13
        max_tokens = $null
        scorer_sha = $scorerSha
        gold_source = "independent"
        cost_usd_estimate = 0.0
        trace_id = $TraceId
        started_at = $StartedAt
        ended_at = $EndedAt
        parameters = @{
            artifact_type = "op1_host_matrix"
            phase = $Phase
            report_path = $ReportPath
            report_sha256 = $reportSha
            preflight_passed = $PreflightPassed
            lab_started = $LabStarted
            case_count = $CaseCount
            passed_count = $PassedCount
            failed_count = $FailedCount
            cleanup_empty = $CleanupEmpty
            volume_deletion = $false
        }
        status = $Status
    }
    $artifact = [ordered]@{op_run_manifest = $manifest; report_path = $ReportPath; report_sha256 = $reportSha}
    $manifestParent = Split-Path -Parent $ManifestPath
    New-Item -ItemType Directory -Force $manifestParent | Out-Null
    $artifact | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 $ManifestPath
}
