from __future__ import annotations

import unittest

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
                for system, user in fake.calls:
                    _validate_prompt_payload(system, user, records_by_id)


if __name__ == "__main__":
    unittest.main()
