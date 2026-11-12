from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_host_matrix_is_isolated_continues_cases_and_never_deletes_volumes():
    script = (ROOT / "lab/run-host-matrix.ps1").read_text(encoding="utf-8-sig")
    scenario_runner = (ROOT / "lab/run-scenario.ps1").read_text(encoding="utf-8-sig")
    stop = (ROOT / "lab/stop.ps1").read_text(encoding="utf-8-sig")
    compose = (ROOT / "lab/compose.yaml").read_text(encoding="utf-8")
    truth = (ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8")
    assert "foreach ($case in $truth.cases)" in script
    assert "opspilot-matrix-" in script and "Set-Content -Encoding utf8 $OutputFile" in script
    assert "finally" in script and "stop.ps1" in script and "ps --all" in script
    assert "$env:COMPOSE_PROJECT_NAME = \"opspilot-matrix-" in script
    assert script.index("$env:COMPOSE_PROJECT_NAME =") < script.index("foreach ($case in $truth.cases)")
    assert scenario_runner.count("docker compose @composeFiles up --build") == 1
    assert "docker compose -f (Join-Path $PSScriptRoot \"compose.yaml\") down" in stop
    assert "-v" not in stop and "docker volume rm" not in stop
    assert "pgdata:/var/lib/postgresql/data" in compose and "redisdata:/data" in compose
    assert "volume_deletion=$false" in script
    assert "-v" not in script and "docker volume rm" not in script
    assert truth.count('"case_id"') == 12
    stage_doc = (ROOT / "docs/learning/stage-OP1.md").read_text(encoding="utf-8")
    assert "当前列出十二个" in stage_doc
    assert "run-host-matrix.ps1" in stage_doc and "docker compose up --build" in stage_doc
    assert "本执行环境无法连接 Docker Desktop Linux Engine" in stage_doc
