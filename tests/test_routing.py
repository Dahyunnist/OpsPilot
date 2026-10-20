import unittest
import json
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from opspilot.routing import select_route
from opspilot.cli import main


class RoutingTests(unittest.TestCase):
    def test_conflicting_strong_candidates_use_single_agent(self):
        report = {"status": "determined", "query_trace": [
            {"source": "metrics", "records": [{"record_id": "m1"}, {"record_id": "m2"}]},
            {"source": "traces", "records": [{"record_id": "t1"}, {"record_id": "t2"}]},
        ], "hypotheses": [
            {"hypothesis_id": "cause_a", "strong_support": ["m1", "t1"]},
            {"hypothesis_id": "cause_b", "strong_support": ["m2", "t2"]},
        ]}
        route, reason = select_route(report)
        self.assertEqual("single-agent", route)
        self.assertIn("Agent", reason)

    def test_abstention_and_one_supported_cause_stay_on_rules(self):
        report = {"status": "insufficient_evidence", "query_trace": [
            {"source": "metrics", "records": [{"record_id": "m1"}]},
            {"source": "traces", "records": [{"record_id": "t1"}]},
        ], "hypotheses": [{"hypothesis_id": "cause_a", "strong_support": ["m1", "t1"]}]}
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
                patch("opspilot.cli.investigate_single_agent", return_value={"mode": "single_agent"}) as single:
            output = StringIO()
            with redirect_stdout(output):
                code = main(["investigate", "case", "--mode", "auto", "--format", "json", "--no-audit", "--no-state"])
        self.assertEqual(0, code)
        self.assertEqual(1, single.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual("single-agent", result["routing"]["selected_mode"])


if __name__ == "__main__":
    unittest.main()
