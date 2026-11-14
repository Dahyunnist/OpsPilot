from __future__ import annotations

import unittest

from opspilot.live_investigation import investigate_live
from scripts.evaluate_op2_frozen import _load_frozen_cases


class FrozenSnapshotTelemetry:
    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    def __getattr__(self, source):
        if source not in self.sources:
            raise AttributeError(source)

        def query(start, end, limit):
            self.calls.append((source, start, end, limit))
            result = self.sources[source]
            return {**result, "records": result.get("records", [])[:limit]}

        return query


class FrozenSnapshotReadonlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _spec, cls.cases = _load_frozen_cases()

    def test_registered_missing_and_contradictory_snapshots_abstain_with_valid_pointers(self):
        expected_ids = {"missing_pool_evidence", "contradictory_payment"}
        selected = [case for case in self.cases if case["spec"]["id"] in expected_ids]
        self.assertEqual(expected_ids, {case["spec"]["id"] for case in selected})

        for case in selected:
            with self.subTest(case=case["spec"]["id"]):
                telemetry = FrozenSnapshotTelemetry(case["sources"])
                report = investigate_live(case["start"], case["end"], limit=100, telemetry=telemetry)
                self.assertEqual([], report["root_causes"])
                self.assertEqual("abstain", report["confidence"])
                self.assertTrue(telemetry.calls)
                self.assertTrue(all(start == case["start"] and end == case["end"] and limit == 100
                    for _source, start, end, limit in telemetry.calls))
                available = {(source, row["record_id"])
                    for source, result in case["sources"].items()
                    for row in result.get("records", [])}
                cited = {(item["source"], item["record_id"])
                    for item in report["evidence_refs"]}
                self.assertLessEqual(cited, available)

    def test_no_evidence_from_registered_snapshots_abstains_without_source_refs(self):
        for case in self.cases:
            with self.subTest(case=case["spec"]["id"]):
                empty = {source: {**result, "records": []}
                    for source, result in case["sources"].items()}
                report = investigate_live(case["start"], case["end"],
                    telemetry=FrozenSnapshotTelemetry(empty))
                self.assertEqual([], report["root_causes"])
                self.assertEqual("abstain", report["confidence"])
                self.assertEqual([], report["evidence_refs"])


if __name__ == "__main__":
    unittest.main()
