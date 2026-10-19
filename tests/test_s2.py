import unittest
from pathlib import Path

from opspilot.cli import load_incidents
from opspilot.investigator import investigate

ROOT = Path(__file__).resolve().parents[1]


class CorrelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.incidents = load_incidents(ROOT / "data" / "incidents.jsonl")

    def test_downstream_trace_counters_database_log_hypothesis(self):
        report = investigate(self.incidents["downstream-01"])
        self.assertEqual("database connection pool exhausted", report["comparison"]["logs_only"]["conclusion"])
        self.assertEqual("downstream service exceeded request timeout", report["conclusion"])
        self.assertIn("pool_exhaustion", {item["hypothesis_id"] for item in report["rejected_hypotheses"]})
        pool = next(item for item in report["hypotheses"] if item["hypothesis_id"] == "pool_exhaustion")
        self.assertIn("downstream-01-m3", pool["against"])
        self.assertIn("downstream-01-t1", pool["against"])

    def test_trace_and_version_links_cross_signal_sources(self):
        report = investigate(self.incidents["downstream-01"])
        trace_links = [link for link in report["correlations"] if link["kind"] == "trace_id"]
        self.assertTrue(any(link["key"] == "tr-downstream-01-a91c" and {"logs", "traces"}.issubset(link["sources"]) for link in trace_links))
        config = investigate(self.incidents["config-01"])
        version_links = [link for link in config["correlations"] if link["kind"] == "version"]
        self.assertTrue(any({"metrics", "logs", "traces", "deployments"}.issubset(link["sources"]) for link in version_links))

    def test_query_strategy_uses_metrics_to_choose_followup(self):
        report = investigate(self.incidents["downstream-01"])
        self.assertEqual("traces", report["query_strategy"]["followups"][0])

    def test_every_evidence_citation_resolves(self):
        for incident_id in self.incidents:
            with self.subTest(incident_id=incident_id):
                self.assertTrue(investigate(self.incidents[incident_id])["citation_validation"]["all_references_exist"])


if __name__ == "__main__":
    unittest.main()
