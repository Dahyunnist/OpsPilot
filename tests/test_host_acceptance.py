from pathlib import Path
import json

from scripts.print_host_acceptance import ROOT, render


def test_host_checklist_covers_all_truth_cases_ports_and_cleanup_without_execution():
    text = render()
    for case_id, fault in (("lab-pool-01", "db_pool_exhaustion"), ("lab-dependency-01", "dependency_timeout"),
                           ("lab-release-01", "bad_release"), ("lab-sql-01", "slow_sql"),
                           ("lab-retry-01", "retry_storm"), ("lab-double-01", "double_root")):
        assert case_id in text and f"-Scenario {fault}" in text
    for value in ("18080", "18081", "18082", "18083", "19090", "16686", "lab\\stop.ps1", "ps --all"):
        assert value in text
    assert "不调用模型" in text and "不删除数据卷" in text


def test_dependency_timeout_host_card_uses_real_read_only_endpoints_and_cleanup():
    text = (ROOT / 'docs/learning/op1-dependency-timeout-host-card.md').read_text(encoding='utf-8')
    for fragment in ('-Scenario dependency_timeout', '18081/logs?', '18080/deployments?', '19090/api/v1/query?', '16686/api/traces?', 'lab\\stop.ps1', 'ps --all', 'truth.json'):
        assert fragment in text
    assert '不表示场景已运行' in text


def test_bad_release_acceptance_orders_utc_window_and_queries_correct_services():
    acceptance = render()
    release = acceptance.split("## lab-release-01 / bad_release", 1)[1].split("\n## ", 1)[0]
    assert release.index("先记录 UTC 起点") < release.index("启动场景")
    assert release.index("启动场景") < release.index("触发：POST")
    assert release.index("触发：POST") < release.index("记录 UTC 终点")
    assert "18081/logs?start=$from&end=$to" in release
    assert "18080/deployments?start=$from&end=$to" in release
    assert "不能证明记录缺失" in release
    assert "AddMinutes(-30)" not in release and "AddMinutes(5)" not in release

    smoke = (ROOT / "lab" / "smoke.ps1").read_text(encoding="utf-8-sig")
    assert smoke.index('$windowStart = [DateTime]::UtcNow.ToString("o")') < smoke.index('& $runScript -Scenario')
    assert smoke.index('& $runScript -Scenario') < smoke.index('http://127.0.0.1:18080/checkout')
    assert smoke.index('http://127.0.0.1:18080/checkout') < smoke.index('$windowEnd = [DateTime]::UtcNow.ToString("o")')
    assert '18081/logs?start=$from&end=$to' in smoke
    assert '18080/deployments?start=$from&end=$to' in smoke
    assert "18081/deployments" not in smoke
    assert '$_.order_id -eq $case.case_id' in smoke


def test_user_host_bad_release_result_preserves_reported_status_and_limits():
    result_path = ROOT / "reports" / "op1-bad-release-host-result-2026-11-13.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["evidence_source"] == "user-provided Windows host terminal summary"
    assert result["checkout"]["http_status"] == 502
    assert result["fault_deployment"] == {
        "service": "orders", "version": "v2", "record_observed": True,
        "host": "127.0.0.1:18080", "path": "/deployments",
    }
    assert result["logs"]["records_returned"] is True
    assert result["logs"]["record_count"] is None
    assert result["cleanup"]["containers_removed"] == 10
    assert result["cleanup"]["networks_removed"] == 1
    assert result["cleanup"]["compose_ps_all_empty"] is True
    assert result["not_recorded"]
    assert "Does not infer detailed root cause" in result["interpretation_limit"]


