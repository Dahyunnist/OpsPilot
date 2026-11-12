from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_host_matrix_is_isolated_continues_cases_and_never_deletes_volumes():
    script = (ROOT / "lab/run-host-matrix.ps1").read_text(encoding="utf-8-sig")
    truth = (ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8")
    assert "foreach ($case in $truth.cases)" in script
    assert "opspilot-matrix-" in script and "Set-Content -Encoding utf8 $OutputFile" in script
    assert "finally" in script and "stop.ps1" in script and "ps --all" in script
    assert "volume_deletion=$false" in script
    assert "-v" not in script and "docker volume rm" not in script
    assert truth.count('"case_id"') == 12
