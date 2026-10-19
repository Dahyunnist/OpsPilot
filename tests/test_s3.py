import json
import tempfile
import unittest
from pathlib import Path

from opspilot.cli import load_incidents
from opspilot.investigator import investigate, query_source
from opspilot.safety import audit_investigation, simulate_action

ROOT = Path(__file__).resolve().parents[1]


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.incidents = load_incidents(ROOT / "data" / "incidents.jsonl")

    def test_query_budget_stops_before_unqueried_sources(self):
        report = investigate(self.incidents["pool-01"], query_budget=2)
        self.assertEqual(2, report["cost"]["query_count"])
        self.assertEqual({"metrics", "traces"}, {query["source"] for query in report["query_trace"]})
        self.assertEqual("insufficient_evidence", report["status"])
        self.assertTrue(any("logs source not queried" in question for item in report["hypotheses"] for question in item["open_questions"]))

    def test_resume_reuses_completed_query_and_finishes_remaining(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "incident.json"
            first = investigate(self.incidents["pool-01"], query_budget=1, state_path=state_path)
            second = investigate(self.incidents["pool-01"], query_budget=8, state_path=state_path)
            self.assertEqual(1, first["cost"]["query_count"])
            self.assertEqual(3, second["cost"]["query_count"])
            self.assertEqual(1, second["cost"]["reused_query_count"])
            self.assertEqual(4, len(second["query_trace"]))
            self.assertEqual("determined", second["status"])

    def test_timeout_duplicate_and_out_of_order_are_handled(self):
        incident = self.incidents["downstream-01"]
        timeout_report = investigate(incident, fault_injections={"logs": "timeout"})
        log_query = next(query for query in timeout_report["query_trace"] if query["source"] == "logs")
        self.assertEqual("timeout", log_query["status"])
        self.assertTrue(any("logs source timeout" in question for item in timeout_report["hypotheses"] for question in item["open_questions"]))

        duplicate_query = query_source(incident, "logs", fault="duplicate")
        self.assertEqual(1, duplicate_query["duplicates_dropped"])
        self.assertEqual(2, duplicate_query["record_count"])

        shuffled_query = query_source(incident, "logs", fault="out_of_order")
        self.assertTrue(shuffled_query["source_was_out_of_order"])
        self.assertEqual(sorted(row["record_id"] for row in shuffled_query["records"]), [row["record_id"] for row in shuffled_query["records"]])

    def test_approval_rejection_has_no_execution_and_duplicate_event_is_idempotent(self):
        report = investigate(self.incidents["config-01"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            rejected = simulate_action(report, "approval-event-1", "reject", path)
            repeated = simulate_action(report, "approval-event-1", "approve", path)
            self.assertEqual("rejected_no_execution", rejected["status"])
            self.assertFalse(rejected["executed"])
            self.assertFalse(rejected["external_write"])
            self.assertEqual("duplicate_ignored", repeated["status"])
            self.assertFalse(repeated["executed"])
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, sum(event.get("event_id") == "approval-event-1" for event in events))

    def test_approved_action_stays_in_simulator(self):
        report = investigate(self.incidents["config-01"])
        with tempfile.TemporaryDirectory() as temporary:
            result = simulate_action(report, "approval-event-2", "approve", Path(temporary) / "audit.jsonl")
            self.assertEqual("simulated_execution", result["status"])
            self.assertTrue(result["executed"])
            self.assertFalse(result["external_write"])

    def test_audit_records_queries_and_report_hash(self):
        report = investigate(self.incidents["pool-01"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            audit_investigation(report, path)
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(5, len(events))
            self.assertEqual(4, sum(event["event_type"] == "readonly_query" for event in events))
            summary = next(event for event in events if event["event_type"] == "investigation_report")
            self.assertEqual(64, len(summary["report_sha256"]))
            self.assertIn("start", events[0]["params"])


if __name__ == "__main__":
    unittest.main()
