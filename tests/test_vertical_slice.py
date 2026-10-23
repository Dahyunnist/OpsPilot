from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.evaluate_vertical_slice import DEFAULT_CASE, independent_gold, load_case, preflight


ROOT = Path(__file__).resolve().parents[1]


class VerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = load_case(DEFAULT_CASE)

    def test_case_truth_is_not_mounted_in_the_services_or_read_tools(self) -> None:
        compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        controller = (ROOT / "lab" / "run-vertical-slice.ps1").read_text(encoding="utf-8")
        self.assertIn("./telemetry:/telemetry:ro", compose)
        self.assertNotIn("scenarios/", compose)
        self.assertNotIn("truth.json", compose)
        self.assertIn("docker image inspect", controller)
        self.assertIn("up --no-build", controller)
        mcp_source = (ROOT / "opspilot" / "mcp_servers.py").read_text(encoding="utf-8")
        self.assertNotIn("vertical-slow-sql.json", mcp_source)
        self.assertNotIn(self.case["case_id"], mcp_source)

    def test_slow_sql_oracle_requires_independent_database_trace_or_metric(self) -> None:
        sources = {
            "traces": {"records": [
                {"record_id": "trace-slow-sql", "attributes": {"operation": "postgres.query", "duration_us": 1_500_000}},
                {"record_id": "trace-payment", "attributes": {"operation": "payment/charge", "duration_us": 2_000_000}},
            ]},
            "metrics": {"records": [
                {"record_id": "metric-slow-sql", "service": "orders", "attributes": {"name": "opspilot_http_duration_ms_sum", "value": 1450}},
                {"record_id": "metric-wrong-service", "service": "payment", "attributes": {"name": "opspilot_http_duration_ms_sum", "value": 1450}},
            ]},
        }
        self.assertEqual(independent_gold(self.case, sources), {"trace-slow-sql", "metric-slow-sql"})

    def test_preflight_estimates_truncation_retry_and_never_sends_truth(self) -> None:
        sources = {
            "metrics": {"records": [{"record_id": "m1", "source": "metrics", "service": "orders",
                "timestamp": "2026-10-23T10:00:00Z", "attributes": {"name": "latency", "value": 1500}, "raw": {}}]},
            "logs": {"records": [{"record_id": "l1", "source": "logs", "service": "orders",
                "timestamp": "2026-10-23T10:00:00Z", "attributes": {"error_type": "TimeoutError"}, "raw": {"message": "slow"}}]},
            "traces": {"records": [{"record_id": "t1", "source": "traces", "service": "orders",
                "timestamp": "2026-10-23T10:00:00Z", "attributes": {"operation": "postgres.query", "duration_us": 1500000}, "raw": {}}]},
            "deployments": {"records": []},
        }
        rows = preflight(self.case["case_id"], "2026-10-23T10:00:00Z", "2026-10-23T10:01:00Z", sources, 512)
        serialized = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn(self.case["case_id"], serialized)
        self.assertNotIn("slow_sql", serialized)
        self.assertNotIn(self.case["oracle"]["independent_questions"][0], serialized)
        self.assertTrue(any(row["attempt"] == "one_truncation_retry" for row in rows))
        self.assertTrue(all(row["reserve_usd"] > 0 for row in rows))

    def test_live_artifact_records_baseline_fault_recovery_without_truth_in_snapshot(self) -> None:
        snapshot = json.loads((ROOT / "reports" / "vertical-slow-sql-snapshot.json").read_text(encoding="utf-8"))
        snapshot_text = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("slow_sql", snapshot_text)
        self.assertNotIn("slow_database_query", snapshot_text)
        report = json.loads((ROOT / "reports" / "vertical-slow-sql-evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual("completed", report["status"])
        timeline = report["window"]
        self.assertEqual((200, 200, 200), (timeline["baseline_response_status"],
            timeline["fault_response_status"], timeline["recovery_response_status"]))
        self.assertGreaterEqual(timeline["fault_request_duration_ms"], 1200)
        self.assertLess(timeline["recovery_request_duration_ms"], timeline["fault_request_duration_ms"])
        self.assertEqual(["slow_database_query"], report["independent_oracle"]["root_causes"])
        self.assertTrue(all(report["evaluation"]["strategies"][name]["metrics"]["exact_root_set"]
            for name in ("rules", "single", "multi", "auto")))


if __name__ == "__main__":
    unittest.main()
