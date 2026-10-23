from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LabManifestTests(unittest.TestCase):
    def test_frozen_scenarios_have_distinct_mechanisms_and_truth_is_separate(self) -> None:
        truth = json.loads((ROOT / "lab" / "scenarios" / "truth.json").read_text(encoding="utf-8"))
        cases = truth["cases"]
        mechanisms = {case["fault"] for case in cases}
        self.assertEqual(len(cases), 6)
        self.assertEqual(len(mechanisms), 6)
        self.assertEqual(len({case["case_id"] for case in cases}), 6)
        self.assertTrue(any(len(case["root_causes"]) > 1 for case in cases))
        for case in cases:
            self.assertTrue(case["must_observe"])
            self.assertTrue(case["root_causes"])

    def test_service_has_no_network_fault_control_route(self) -> None:
        source = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("/inject", source)
        self.assertNotIn("/clear-fault", source)


if __name__ == "__main__":
    unittest.main()
