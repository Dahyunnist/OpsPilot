from pathlib import Path


def test_op1_cleanup_wrapper_always_stops_and_checks_without_deleting_evidence():
    root = Path(__file__).resolve().parents[1]
    wrapper = (root / "lab/run-op1-case-clean.ps1").read_text(encoding="utf-8")
    stop = (root / "lab/stop.ps1").read_text(encoding="utf-8")
    assert "finally" in wrapper
    assert '"stop.ps1"' in wrapper and "ps --all" in wrapper
    assert "OutputFile $OutputFile" in wrapper and "SnapshotFile $SnapshotFile" in wrapper
    assert "Remove-Item" not in wrapper and "-v" not in stop
    assert "exit $runExit" in wrapper
