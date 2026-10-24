from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from opspilot.live_investigation import investigate_live
from opspilot.strategy_eval import _auto_route

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "slow_sql": ("slow_database_query", "vertical-slow-sql-snapshot.json", "vertical-slow-sql-window.json"),
    "db_pool_exhaustion": ("database_pool_exhaustion", "vertical-db-pool-exhaustion-snapshot.json", "vertical-db-pool-exhaustion-window.json"),
    "dependency_timeout": ("payment_dependency_timeout", "vertical-dependency-timeout-snapshot.json", "vertical-dependency-timeout-window.json"),
}


def inspect(sources, window):
    bounds = window.get("assessment_window", {})
    start = bounds.get("start", window["fault_window_start"])
    end = bounds.get("end", window["fault_window_end"])
    telemetry = type("Frozen", (), {"__getattr__": lambda self, key: lambda *_: sources[key]})()
    return investigate_live(start, end, telemetry=telemetry)


class TelemetryRobustnessTests(unittest.TestCase):
    def load(self, case):
        _, snapshot, window = CASES[case]
        sources = json.loads((ROOT / "reports" / snapshot).read_text(encoding="utf-8-sig"))
        timeline = json.loads((ROOT / "reports" / window).read_text(encoding="utf-8-sig"))["timeline"]
        return sources, timeline

    def test_normal_evidence_and_missing_evidence_contract(self):
        for case in ("slow_sql", "db_pool_exhaustion"):
            with self.subTest(case=case):
                sources, window = self.load(case)
                root = CASES[case][0]
                self.assertIn(root, inspect(sources, window)["root_causes"])
                missing = copy.deepcopy(sources)
                missing["logs"]["records"] = []
                missing["traces"]["records"] = []
                self.assertNotIn(root, inspect(missing, window)["root_causes"])

    def test_out_of_order_records_are_order_independent(self):
        for case in CASES:
            with self.subTest(case=case):
                sources, window = self.load(case)
                expected = inspect(sources, window)["root_causes"]
                shuffled = copy.deepcopy(sources)
                for source in shuffled.values():
                    source["records"].reverse()
                self.assertEqual(expected, inspect(shuffled, window)["root_causes"])

    def test_conflicting_payment_spans_cause_abstention(self):
        sources, window = self.load("dependency_timeout")
        report = inspect(sources, window)
        self.assertNotIn("payment_dependency_timeout", report["root_causes"])
        self.assertEqual("abstain", report["confidence"])

    def test_same_trace_clock_drift_is_reported(self):
        sources, window = self.load("dependency_timeout")
        spans = sources["traces"]["records"]
        trace_id = next(row["trace_id"] for row in spans if row.get("trace_id"))
        logs = sources["logs"]["records"]
        synthetic = {"record_id": "logs:drift", "timestamp": "2026-10-23T13:00:00Z", "source": "logs",
            "service": "orders", "trace_id": trace_id, "attributes": {},
            "raw": {"timestamp": "2026-10-23T13:00:00Z", "message": "request completed", "severity": "info"}}
        sources = copy.deepcopy(sources)
        sources["logs"]["records"].append(synthetic)
        report = inspect(sources, window)
        # The investigation report exposes skew details in its diagnostic field.
        self.assertTrue(any(item["seconds"] > 2 for item in report.get("quality", {}).get("same_trace_clock_skews", [])))

    def test_auto_route_boundaries(self):
        def candidate(count):
            return {"root_causes": [], "hypotheses": [
                {"support": [{"record_id": "evidence"}], "independent_sources": ["logs", "traces"]}
                for _ in range(count)]}
        self.assertEqual("rules", _auto_route(candidate(0)))
        self.assertEqual("single", _auto_route(candidate(1)))
        self.assertEqual("multi", _auto_route(candidate(2)))
        self.assertEqual("rules", _auto_route({"root_causes": ["slow_database_query"], "hypotheses": []}))

    def test_previous_auto_transport_failure_is_kept(self):
        report = json.loads((ROOT / "reports/vertical-dependency-timeout-evaluation.json").read_text(encoding="utf-8-sig"))
        auto = report["evaluation"]["strategies"]["auto"]
        self.assertEqual("failed", auto["evaluation_status"])
        self.assertIn("ConnectionResetError", auto["failures"])

    def test_snapshots_do_not_contain_injection_truth(self):
        for case, (_, snapshot, _) in CASES.items():
            with self.subTest(case=case):
                data = (ROOT / "reports" / snapshot).read_text(encoding="utf-8-sig").lower()
                for forbidden in ('"oracle"', '"fault"', '"root_causes"'):
                    self.assertNotIn(forbidden, data)


if __name__ == "__main__":
    unittest.main()
