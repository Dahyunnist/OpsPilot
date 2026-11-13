from __future__ import annotations

from pathlib import Path

from lab.service.time_window import in_window


ROOT = Path(__file__).resolve().parents[1]


def test_bad_release_deployment_and_log_use_same_half_open_utc_window():
    deployment = "2026-11-11T13:47:23Z"
    log = "2026-11-11T14:06:22.691Z"
    start, end = "2026-11-11T13:47:00Z", "2026-11-11T14:07:00Z"
    assert in_window(deployment, start, end)
    assert in_window(log, start, end)

    # User's later five-minute log window contains the log, but not this deployment.
    later_start, later_end = "2026-11-11T14:06:00Z", "2026-11-11T14:11:00Z"
    assert in_window(log, later_start, later_end)
    assert not in_window(deployment, later_start, later_end)


def test_utc_window_is_start_inclusive_and_end_exclusive():
    start, end = "2026-11-11T13:47:23Z", "2026-11-11T13:48:23Z"
    assert in_window(start, start, end)
    assert not in_window(end, start, end)
    assert in_window("2026-11-11T14:47:23+01:00", start, end)


def test_logs_and_deployments_share_service_window_filter_and_documented_ports():
    source = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
    assert "from time_window import in_window, parse_utc" in source
    assert source.count("in_window(") == 2
    assert 'if path.path == "/logs":' in source
    assert 'if path.path == "/deployments" and SERVICE == "gateway":' in source
    logs_route = source.split('if path.path == "/logs":', 1)[1].split('if path.path == "/deployments"', 1)[0]
    deployments_route = source.split('if path.path == "/deployments"', 1)[1]
    assert 'in_window(row["timestamp"], start, end)' in logs_route
    assert 'in_window(row.get("timestamp", ""), start, end)' in deployments_route
    compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
    gateway = compose.split("  gateway:", 1)[1].split("  orders:", 1)[0]
    orders = compose.split("  orders:", 1)[1].split("  inventory:", 1)[0]
    assert '"127.0.0.1:18080:8080"' in gateway
    assert '"127.0.0.1:18081:8080"' in orders
    dockerfile = (ROOT / "lab" / "service" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY time_window.py ./time_window.py" in dockerfile

    acceptance = (ROOT / "docs" / "learning" / "op1-bad-release-host-card.md").read_text(encoding="utf-8")
    assert "18081/logs?start=$from&end=$to" in acceptance
    assert "18080/deployments?start=$from&end=$to" in acceptance
    assert "start <= deployment timestamp < end" in acceptance
