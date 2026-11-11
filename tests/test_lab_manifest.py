from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
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

    def test_op1_runner_refuses_to_report_results_without_required_local_images(self) -> None:
        runner = (ROOT / "lab" / "run-op1-case.ps1").read_text(encoding="utf-8")
        self.assertIn("docker image inspect", runner)
        self.assertIn("Missing local image; refusing build or pull", runner)
        self.assertIn("--no-build", runner)
        self.assertLess(runner.index("docker image inspect"), runner.index("docker compose -f $composeFile up"))

    def test_op1_truth_is_not_mounted_into_compose_services(self) -> None:
        compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        self.assertNotIn("truth.json", compose)
        self.assertIn("LAB_FAULT: ${LAB_FAULT:-none}", compose)


if __name__ == "__main__":
    unittest.main()
