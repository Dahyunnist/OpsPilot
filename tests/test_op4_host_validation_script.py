from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_bad_release_host_script_is_single_request_and_scoped_to_its_own_project():
    source = (ROOT / "lab" / "verify-bad-release.ps1").read_text(encoding="utf-8")
    assert source.count("Invoke-WebRequest -Method Post") == 1
    assert '"http://127.0.0.1:18080/checkout"' in source
    assert "docker compose --project-name $projectName" in source
    assert 'docker compose --project-name $projectName -f $composeFile up --build -d' in source
    assert 'docker compose --project-name $projectName -f $composeFile ps --all --quiet' in source
    assert 'Invoke-Compose @("down")' in source
    assert "down -v" not in source
    assert '$result.cleanup.volumes_deleted = $false' in source


def test_bad_release_host_script_uses_same_encoded_utc_window_and_proves_run_identity():
    source = (ROOT / "lab" / "verify-bad-release.ps1").read_text(encoding="utf-8")
    assert '$result.window_start_utc = [DateTime]::UtcNow.ToString("o")' in source
    assert '$result.window_end_utc = [DateTime]::UtcNow.ToString("o")' in source
    assert '[uri]::EscapeDataString($result.window_start_utc)' in source
    assert '[uri]::EscapeDataString($result.window_end_utc)' in source
    assert '18081/logs?start=$from&end=$to' in source
    assert '18080/deployments?start=$from&end=$to' in source
    assert '$_.order_id -eq $orderId' in source
    assert '$_.run_id -eq $runId -and $_.order_id -eq $orderId' in source
    assert '$result.deployment.version -ne "v2"' in source


def test_dns_evidence_scalar_is_counted_in_windows_powershell_51():
    executable = shutil.which("powershell.exe")
    if not executable:
        import pytest
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    source = (ROOT / "lab" / "verify-bad-release.ps1").read_text(encoding="utf-8")
    assert "$dnsEvidence = @($matchingLogs | Where-Object {" in source
    assert "Test-InventoryDnsEvidence -Record $_" in source
    assert "$dnsEvidence.Count -eq 0" in source
    powershell = r"""
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) {
    throw "Expected Windows PowerShell 5.1, got $($PSVersionTable.PSVersion)."
}
$matchingLogs = @(
    [pscustomobject]@{ dependency_host = 'orders'; error_reason = '[Errno -5] No address associated with hostname' }
    [pscustomobject]@{ dependency_host = 'inventory-v2'; error_reason = '[Errno -5] No address associated with hostname' }
    [pscustomobject]@{ dependency_host = 'database'; error_reason = '' }
)
$dnsEvidence = @($matchingLogs | Where-Object {
    $dependencyHost = [string]$_.dependency_host
    $errorReason = [string]$_.error_reason
    $dependencyHost -eq 'inventory-v2' -or
    $errorReason -match 'inventory-v2|Name or service not known|No address associated with hostname'
})
$reasonOnlyEvidence = @($dnsEvidence | Where-Object { $_.dependency_host -ne 'inventory-v2' })
if ($dnsEvidence.Count -ne 2 -or $reasonOnlyEvidence.Count -ne 1) {
    throw "Expected two DNS records including one error_reason-only record, found $($dnsEvidence.Count)."
}
Write-Output "Windows PowerShell 5.1 DNS evidence count: $($dnsEvidence.Count); reason-only: $($reasonOnlyEvidence.Count)"
"""
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command", powershell],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Windows PowerShell 5.1 DNS evidence count: 2; reason-only: 1" in result.stdout


def test_invoke_restmethod_records_wrapper_with_one_dns_row_passes_ps51_filter():
    executable = shutil.which("powershell.exe")
    if not executable:
        import pytest
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    powershell = r'''
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) {
    throw "Expected Windows PowerShell 5.1, got $($PSVersionTable.PSVersion)."
}
$orderId = 'bad-release-8a23a2cd-779d-4d65-9027-bb12cc9e673b'
$module = Join-Path (Get-Location) 'lab\telemetry-evidence.psm1'
Import-Module $module -Force
$json = @'
{"records":[
 {"order_id":"bad-release-8a23a2cd-779d-4d65-9027-bb12cc9e673b","dependency_host":"inventory-v2","error_reason":"[Errno -5] No address associated with hostname"},
 {"order_id":"bad-release-8a23a2cd-779d-4d65-9027-bb12cc9e673b","error_type":"RuntimeError"},
 {"order_id":"another-order","dependency_host":"inventory-v2","error_reason":"No address associated with hostname"}
]}
'@
# Invoke-RestMethod returns this PSCustomObject wrapper with records collection.
$logResponse = ConvertFrom-Json -InputObject $json
$matchingLogs = @($logResponse.records | Where-Object { $_.order_id -eq $orderId })
$dnsEvidence = @($matchingLogs | Where-Object { Test-InventoryDnsEvidence -Record $_ })
if ($matchingLogs.Count -ne 2 -or $dnsEvidence.Count -ne 1) {
    throw "Expected two order logs and one DNS row, got $($matchingLogs.Count) and $($dnsEvidence.Count)."
}
Write-Output "Invoke-RestMethod wrapper matched 2 order logs and 1 DNS row."
$nested = [pscustomobject]@{ order_id = $orderId; attributes = @{ dependency_host = ' inventory-v2 '; error_reason = "[Errno -5] No address" + [char]10 + "associated with hostname" } }
if (-not (Test-InventoryDnsEvidence -Record $nested)) { throw 'Nested attributes DNS evidence was not detected.' }
Write-Output "Nested PowerShell attributes matched the inventory-v2 DNS evidence."
$raw = @{ order_id = $orderId; raw = @{ dependency_host = 'inventory-v2' } }
if (-not (Test-InventoryDnsEvidence -Record $raw)) { throw 'Dictionary raw DNS evidence was not detected.' }
Write-Output "Dictionary raw fields matched the inventory-v2 DNS evidence."
'''
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command", powershell],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Invoke-RestMethod wrapper matched 2 order logs and 1 DNS row." in result.stdout
    assert "Nested PowerShell attributes matched the inventory-v2 DNS evidence." in result.stdout
    assert "Dictionary raw fields matched the inventory-v2 DNS evidence." in result.stdout


def test_host_validation_guide_lists_each_blocked_socket_test_separately():
    guide = (ROOT / "docs" / "learning" / "op4-host-validation.md").read_text(encoding="utf-8")
    commands = [line for line in guide.splitlines() if line.startswith("& .\\.venv\\Scripts\\python.exe -m pytest")]
    assert len(commands) == 9
