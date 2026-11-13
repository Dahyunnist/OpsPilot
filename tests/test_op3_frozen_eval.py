from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.evaluate_op2_frozen import STRATEGIES, _load_frozen_cases, main


class FrozenOp3EvaluationTests(unittest.TestCase):
    def test_preregistered_snapshots_and_shared_strategy_order_are_frozen(self):
        spec, cases = _load_frozen_cases()
        self.assertEqual(["rules", "single", "multi", "auto"], list(STRATEGIES))
        self.assertEqual(3, len(cases))
        self.assertEqual(3, len({case["snapshot_sha256"] for case in cases}))
        self.assertTrue(all(case["start"] < case["end"] for case in cases))
        self.assertEqual(spec["total_worst_case_usd"], 0.0230226)
        self.assertEqual(spec["calls_made"], 0)

    def test_real_run_requires_explicit_execute_switch(self):
        with patch("sys.argv", ["evaluate_op2_frozen.py"]), self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
