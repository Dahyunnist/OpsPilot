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
        self.assertEqual(len(cases), 10)
        self.assertEqual(len(mechanisms), 10)
        case_ids = {case["case_id"] for case in cases}
        self.assertEqual(len(case_ids), len(cases))
        self.assertEqual(case_ids, {case["case_id"] for case in truth["cases"]})
        self.assertTrue(any(len(case["root_causes"]) > 1 for case in cases))
        for case in cases:
            self.assertTrue(case["must_observe"])
            self.assertTrue(case["root_causes"])

    def test_new_fault_mechanisms_have_distinct_injection_and_truth_signals(self) -> None:
        truth = json.loads((ROOT / "lab" / "scenarios" / "truth.json").read_text(encoding="utf-8"))
        cases = {case["fault"]: case for case in truth["cases"]}
        self.assertEqual(["inventory_dependency_latency"], cases["inventory_latency"]["root_causes"])
        self.assertEqual(["payment_service_rejection"], cases["payment_reject"]["root_causes"])
        source = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
        self.assertIn('FAULT == "inventory_latency"', source)
        self.assertIn('FAULT == "payment_reject"', source)
        self.assertIn('time.sleep(0.8)', source)
        self.assertIn('ResponseError(503, "synthetic payment rejection")', source)
        runner = (ROOT / "lab" / "run-scenario.ps1").read_text(encoding="utf-8")
        self.assertIn('"inventory_latency"', runner)
        self.assertIn('"payment_reject"', runner)
        self.assertIn('FAULT == "stale_inventory"', source)
        self.assertIn('FAULT == "duplicate_delivery"', source)
        self.assertIn("duplicate delivery replayed payment", source)
        self.assertIn('"stale_inventory"', runner)
        self.assertIn('"duplicate_delivery"', runner)

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

    def test_live_telemetry_sources_do_not_read_truth_manifest(self) -> None:
        for name in ("telemetry.py", "live_investigation.py", "mcp_servers.py"):
            source = (ROOT / "opspilot" / name).read_text(encoding="utf-8")
            self.assertNotIn("scenarios/truth.json", source)
            self.assertNotIn("lab/scenarios/truth.json", source)

    def test_release_fault_fixture_has_evidence_contract(self) -> None:
        truth = json.loads((ROOT / "lab" / "scenarios" / "truth.json").read_text(encoding="utf-8"))
        release = next(case for case in truth["cases"] if case["fault"] == "bad_release")
        self.assertEqual(["invalid_release_configuration"], release["root_causes"])
        self.assertEqual({"orders_v2", "inventory_connection_errors"}, set(release["must_observe"]))
        source = (ROOT / "opspilot" / "live_investigation.py").read_text(encoding="utf-8")
        self.assertIn('"invalid_release_configuration"', source)
        self.assertIn("orders v2", source)


if __name__ == "__main__":
    unittest.main()
