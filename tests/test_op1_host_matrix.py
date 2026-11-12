from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_host_matrix_is_isolated_continues_cases_and_never_deletes_volumes():
    script = (ROOT / "lab/run-host-matrix.ps1").read_text(encoding="utf-8-sig")
    scenario_runner = (ROOT / "lab/run-scenario.ps1").read_text(encoding="utf-8-sig")
    stop = (ROOT / "lab/stop.ps1").read_text(encoding="utf-8-sig")
    compose = (ROOT / "lab/compose.yaml").read_text(encoding="utf-8")
    truth = (ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8")
    assert "foreach ($case in $truth.cases)" in script
    assert "opspilot-matrix-" in script and "Set-Content -Encoding utf8 $OutputFile" in script
    assert "docker info --format" in script and "docker image inspect $image" in script
    assert "Missing local image; refusing network pull" in script and "config --format json" in script
    assert script.index("docker info --format") < script.index("$runScript -Scenario none")
    assert "Get-NetTCPConnection -State Listen -LocalPort $port" in script
    assert "finally" in script and "stop.ps1" in script and "ps --all" in script
    assert "$env:COMPOSE_PROJECT_NAME = \"opspilot-matrix-" in script
    assert script.index("$env:COMPOSE_PROJECT_NAME =") < script.index("foreach ($case in $truth.cases)")
    assert scenario_runner.count("docker compose @composeFiles up --build") == 1
    assert "function Wait-LabReady([int]$TimeoutSeconds = 60)" in scenario_runner
    assert scenario_runner.count("\nWait-LabReady\n") == 1
    assert scenario_runner.index("\nWait-LabReady\n") < scenario_runner.index('Write-Host "Scenario ready:')
    assert "docker compose @composeFiles down" in stop
    assert "-v" not in stop and "docker volume rm" not in stop
    assert "pgdata:/var/lib/postgresql/data" in compose and "redisdata:/data" in compose
    assert "volume_deletion=$false" in script
    assert re.search(r"(?:^|\s)-v(?:\s|$)", script) is None and "docker volume rm" not in script
    assert truth.count('"case_id"') == 12
    stage_doc = (ROOT / "docs/learning/stage-OP1.md").read_text(encoding="utf-8")
    assert "当前列出十二个" in stage_doc
    assert "run-host-matrix.ps1" in stage_doc and "docker compose up --build" in stage_doc
    assert "本执行环境无法连接 Docker Desktop Linux Engine" in stage_doc
    assert '18081/logs?start=$from&end=$settledTo' in script
    assert '18080/deployments?start=$from&end=$to' in script
    assert "normal_control=$normalControl" in script and "-Scenario none" in script
    assert "$priorLabFault = $env:LAB_FAULT" in script
    assert "$env:LAB_FAULT=$priorLabFault" in script
    assert "expected_deployment_observed" in script and "scenario_passed=$scenarioPassed" in script
    assert "$settleAttempts=if ($case.fault -eq \"bad_release\") { 10 } else { 2 }" in script
    assert "for ($settle=0; $settle -le $settleAttempts; $settle++)" in script
    assert "@(502, 504)" in script and "expected_http_statuses=$expectedStatuses" in script
