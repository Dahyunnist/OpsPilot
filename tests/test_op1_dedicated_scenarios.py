from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_slow_sql_and_retry_scenarios_have_isolated_outputs_and_finally_cleanup():
    cases = {
        "slow_sql": ("lab/run-slow-sql-case.ps1", ("pg_sleep", "postgres.query", "slow_sql evidence missing"), "op1-slow-sql-host.json"),
        "retry_storm": ("lab/run-retry-storm-case.ps1", "expected at least 3 failed attempts", "op1-retry-storm-host.json"),
    }
    for scenario, (relative, signal, output) in cases.items():
        script = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert f"-Scenario {scenario}" in script
        if isinstance(signal, tuple):
            assert all(value in script for value in signal)
        else:
            assert signal in script
        assert output in script
        assert "finally" in script
        assert '"stop.ps1"' in script
        assert "ps --all" in script
        assert "truth.json" not in script
        assert "docker volume rm" not in script
