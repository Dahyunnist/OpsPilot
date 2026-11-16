from __future__ import annotations

import unittest
import json

from opspilot.strategy_eval import STRATEGIES, evaluate_snapshot, score_case
from scripts.evaluate_lab import _gold_evidence
from scripts.evaluate_op2_frozen import _load_frozen_cases, _validate_prompt_payload


class OfflineFrozenStrategyTests(unittest.TestCase):
    def test_registered_snapshots_run_all_strategies_with_fake_without_gold_leak(self):
        _spec, cases = _load_frozen_cases()

        class AbstainingFake:
            model = "offline-abstaining-fixture"
            max_tokens = 512

            def __init__(self):
                self.calls = []

            def complete(self, system, user, _budget):
                self.calls.append((system, user))
                if system.startswith("You are an independent "):
                    data = {"hypotheses": [], "missing_data": []}
                else:
                    data = {"root_causes": [], "evidence_by_root": {},
                        "uncertainty": "controlled offline fake abstains"}
                return {"data": data, "usage": {"prompt_tokens": "12",
                    "completion_tokens": "4", "total_tokens": "16",
                    "prompt_tokens_details": {"cached_tokens": "2"}}}

        for case in cases:
            with self.subTest(case_id=case["spec"]["id"]):
                fake = AbstainingFake()
                gate = lambda operation, instance, system, user, budget: operation(instance, system, user, budget)
                result = evaluate_snapshot(case["spec"]["id"], case["start"], case["end"],
                    case["sources"], fake, gate, STRATEGIES)
                scored = score_case(result, case["spec"]["root_causes"],
                    _gold_evidence(case["spec"]["root_causes"], case["sources"]))

                self.assertEqual(set(STRATEGIES), set(scored["strategies"]))
                self.assertEqual(result["snapshot_sha256"], scored["snapshot_sha256"])
                self.assertTrue(all(item["evaluation_status"] == "complete"
                    for item in scored["strategies"].values()))
                self.assertEqual(0, scored["strategies"]["rules"]["model_calls"])
                self.assertEqual(1, scored["strategies"]["single"]["model_calls"])
                self.assertGreater(scored["strategies"]["multi"]["model_calls"], 1)
                self.assertTrue(all(not item["prediction"]["root_causes"]
                    for item in scored["strategies"].values() if item["selected_route"] != "rules"))

                records_by_id = {row["record_id"]: row for source in case["sources"].values()
                    for row in source.get("records", [])}
                expected_by_source = {source: [row["record_id"] for row in result["records"][:250]]
                    for source, result in case["sources"].items()}
                payloads = [json.loads(user) for _system, user in fake.calls]
                for system, user in fake.calls:
                    _validate_prompt_payload(system, user, records_by_id)
                single_payloads = [payload for payload in payloads if "live_evidence_by_source" in payload]
                expert_payloads = [payload for payload in payloads if "source" in payload and "records" in payload]
                adjudicator_payloads = [payload for payload in payloads if "all_evidence_ids" in payload]
                if single_payloads:
                    self.assertEqual(expected_by_source,
                        {source: [row["record_id"] for row in rows]
                         for source, rows in single_payloads[0]["live_evidence_by_source"].items()})
                self.assertEqual(expected_by_source,
                    {source: [row["record_id"] for payload in expert_payloads
                              for row in payload["records"] if payload["source"] == source]
                     for source in expected_by_source})
                self.assertTrue(adjudicator_payloads)
                self.assertEqual(set(records_by_id), set(adjudicator_payloads[0]["all_evidence_ids"]))

    def test_malformed_usage_field_is_traceable_for_frozen_multi_strategy(self):
        _spec, cases = _load_frozen_cases()

        class MalformedUsageFake:
            model = "offline-malformed-usage-fixture"
            max_tokens = 512

            def complete(self, system, _user, _budget):
                data = ({"hypotheses": [], "missing_data": []}
                    if system.startswith("You are an independent ")
                    else {"root_causes": [], "evidence_by_root": {},
                          "uncertainty": "offline error-path fixture"})
                return {"data": data, "usage": {"prompt_tokens": 12, "completion_tokens": 4,
                    "total_tokens": 16, "prompt_tokens_details": "not-json"}}

        gate = lambda operation, instance, system, user, budget: operation(instance, system, user, budget)
        for case in cases:
            with self.subTest(case_id=case["spec"]["id"]):
                result = evaluate_snapshot(case["spec"]["id"], case["start"], case["end"],
                    case["sources"], MalformedUsageFake(), gate, ("multi",))
                multi = result["strategies"]["multi"]
                self.assertEqual("failed", multi["evaluation_status"])
                self.assertIn("provider_usage:prompt_tokens_details", multi["failures"])
                self.assertTrue(multi["fallback_route"] == "rules")
                self.assertTrue(any(trace == {"category": "provider_usage",
                    "code": "prompt_tokens_details", "retryable": False}
                    for trace in multi["error_trace"]))


if __name__ == "__main__":
    unittest.main()
