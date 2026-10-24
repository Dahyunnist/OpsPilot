import json
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path

from opspilot.cli import load_incidents
from opspilot.investigator import investigate, query_source

ROOT = Path(__file__).resolve().parents[1]


class InvestigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.incidents = load_incidents(ROOT / "data" / "incidents.jsonl")

    def test_two_root_causes_have_evidence_backed_conclusions(self):
        for incident_id, expected in (
            ("pool-01", "database connection pool exhausted"),
            ("config-01", "deployment introduced invalid order service configuration"),
        ):
            with self.subTest(incident_id=incident_id):
                report = investigate(self.incidents[incident_id])
                self.assertEqual("determined", report["status"])
                self.assertEqual(expected, report["conclusion"])
                self.assertGreaterEqual(len(report["hypotheses"][0]["support"]), 2)

    def test_insufficient_cases_do_not_guess(self):
        for incident_id in ("unknown-01", "unknown-02"):
            with self.subTest(incident_id=incident_id):
                report = investigate(self.incidents[incident_id])
                self.assertEqual("insufficient_evidence", report["status"])
                self.assertIsNone(report["conclusion"])

    def test_missing_log_source_remains_explicit(self):
        report = investigate(self.incidents["pool-01"], {"logs"})
        log_query = next(query for query in report["query_trace"] if query["source"] == "logs")
        self.assertEqual("missing", log_query["status"])
        self.assertEqual([], log_query["records"])
        self.assertTrue(any("logs source missing" in question for hypothesis in report["hypotheses"] for question in hypothesis["open_questions"]))

    def test_query_is_time_bounded_and_has_complete_parameters(self):
        query = query_source(self.incidents["pool-01"], "metrics", max_rows=2)
        self.assertEqual(2, query["record_count"])
        self.assertEqual({"incident_id", "start", "end", "limit"}, set(query["params"]))
        self.assertTrue(all(query["params"]["start"] <= row["timestamp"] <= query["params"]["end"] for row in query["records"]))

    def test_bad_limits_and_unknown_incidents_are_rejected(self):
        with self.assertRaises(ValueError):
            query_source(self.incidents["pool-01"], "metrics", max_rows=0)
        with self.assertRaises(FileNotFoundError):
            load_incidents(ROOT / "data" / "missing.jsonl")

    def test_non_object_jsonl_rows_report_their_line(self):
        for value in ("[]", "null", '"incident"', "42"):
            with self.subTest(value=value), TemporaryDirectory() as directory:
                path = Path(directory) / "incidents.jsonl"
                path.write_text('{"incident_id":"valid"}\n' + value + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "expected JSON object on line 2"):
                    load_incidents(path)


if __name__ == "__main__":
    unittest.main()
