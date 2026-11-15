from pathlib import Path


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


def test_host_validation_guide_lists_each_blocked_socket_test_separately():
    guide = (ROOT / "docs" / "learning" / "op4-host-validation.md").read_text(encoding="utf-8")
    commands = [line for line in guide.splitlines() if line.startswith("& .\\.venv\\Scripts\\python.exe -m pytest")]
    assert len(commands) == 9
