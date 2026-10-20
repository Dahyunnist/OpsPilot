import unittest
import json
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from opspilot.routing import select_route
from opspilot.cli import main


class RoutingTests(unittest.TestCase):
    def test_multiple_independent_strong_candidates_use_multi_agent(self):
        report = {"status": "determined", "query_trace": [
            {"source": "metrics", "records": [{"record_id": "m1"}, {"record_id": "m2"}]},
            {"source": "traces", "records": [{"record_id": "t1"}, {"record_id": "t2"}]},
        ], "hypotheses": [
            {"hypothesis_id": "cause_a", "strong_support": ["m1", "t1"]},
            {"hypothesis_id": "cause_b", "strong_support": ["m2", "t2"]},
        ]}
        route, reason = select_route(report)
        self.assertEqual("multi-agent", route)
        self.assertIn("多 Agent", reason)

    def test_abstention_with_one_strong_candidate_uses_single_agent(self):
        report = {"status": "insufficient_evidence", "query_trace": [
            {"source": "metrics", "records": [{"record_id": "m1"}]},
            {"source": "traces", "records": [{"record_id": "t1"}]},
        ], "hypotheses": [{"hypothesis_id": "cause_a", "strong_support": ["m1", "t1"]}]}
        self.assertEqual("single-agent", select_route(report)[0])

    def test_abstention_without_strong_candidate_stays_on_rules(self):
        report = {"status": "insufficient_evidence", "query_trace": [
            {"source": "metrics", "records": [{"record_id": "m1"}]},
            {"source": "traces", "records": [{"record_id": "t1"}]},
        ], "hypotheses": [{"hypothesis_id": "cause_a", "strong_support": []}]}
        self.assertEqual("rules", select_route(report)[0])

    def test_auto_cli_dispatches_selected_route(self):
        report = {"status": "determined", "query_trace": [
            {"source": "metrics", "records": [{"record_id": "m1"}, {"record_id": "m2"}]},
            {"source": "traces", "records": [{"record_id": "t1"}, {"record_id": "t2"}]},
        ], "hypotheses": [
            {"hypothesis_id": "cause_a", "strong_support": ["m1", "t1"]},
            {"hypothesis_id": "cause_b", "strong_support": ["m2", "t2"]},
        ]}
        with patch("opspilot.cli.load_incidents", return_value={"case": {"incident_id": "case"}}), \
                patch("opspilot.cli.investigate", return_value=report), \
                patch("opspilot.cli.investigate_multi_agent", return_value={"mode": "multi_agent"}) as multi:
            output = StringIO()
            with redirect_stdout(output):
                code = main(["investigate", "case", "--mode", "auto", "--format", "json", "--no-audit", "--no-state"])
        self.assertEqual(0, code)
        self.assertEqual(1, multi.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual("multi-agent", result["routing"]["selected_mode"])


if __name__ == "__main__":
    unittest.main()
