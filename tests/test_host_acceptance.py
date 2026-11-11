from scripts.print_host_acceptance import render


def test_host_checklist_covers_all_truth_cases_ports_and_cleanup_without_execution():
    text = render()
    for case_id, fault in (("lab-pool-01", "db_pool_exhaustion"), ("lab-dependency-01", "dependency_timeout"),
                           ("lab-release-01", "bad_release"), ("lab-sql-01", "slow_sql"),
                           ("lab-retry-01", "retry_storm"), ("lab-double-01", "double_root")):
        assert case_id in text and f"-Scenario {fault}" in text
    for value in ("18080", "18081", "18082", "18083", "19090", "16686", "lab\\stop.ps1", "ps --all"):
        assert value in text
    assert "不调用模型" in text and "不删除数据卷" in text
