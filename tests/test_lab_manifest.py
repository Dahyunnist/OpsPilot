from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LabManifestTests(unittest.TestCase):
    def test_compose_contains_all_required_application_and_telemetry_services(self) -> None:
        text = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        service_block = text.split("services:", 1)[1].split("\nnetworks:\n", 1)[0]
        service_names = {line.strip()[:-1] for line in service_block.splitlines()
            if line.startswith("  ") and line.strip().endswith(":") and not line.startswith("    ")}
        self.assertEqual({"gateway", "orders", "inventory", "payment", "postgres", "redis",
            "otel-collector", "jaeger", "prometheus"}, service_names)

    def test_frozen_scenarios_have_distinct_mechanisms_and_truth_is_separate(self) -> None:
        truth = json.loads((ROOT / "lab" / "scenarios" / "truth.json").read_text(encoding="utf-8"))
        cases = truth["cases"]
        mechanisms = {case["fault"] for case in cases}
        self.assertEqual(len(cases), 12)
        self.assertEqual(len(mechanisms), 12)
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
        self.assertIn('FAULT == "redis_unavailable"', source)
        self.assertIn('FAULT == "inventory_depleted"', source)
        self.assertIn("synthetic redis unavailable", source)
        self.assertIn('"redis_unavailable"', runner)
        self.assertIn('"inventory_depleted"', runner)

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

    def test_bad_release_is_the_only_intentional_unknown_inventory_service(self) -> None:
        truth = json.loads((ROOT / "lab" / "scenarios" / "truth.json").read_text(encoding="utf-8"))
        compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        runner = (ROOT / "lab" / "run-scenario.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("INVENTORY_URL: ${LAB_INVENTORY_URL:-http://inventory:8080}", compose)
        self.assertIn("inventory:\n", compose)
        self.assertNotIn("inventory-v2:\n", compose)
        self.assertIn('if ($Scenario -eq "bad_release") { "http://inventory-v2:8080" } else { "http://inventory:8080" }', runner)
        self.assertIn('ValidateSet("none",', runner)
        release = next(case for case in truth["cases"] if case["fault"] == "bad_release")
        self.assertEqual(["invalid_release_configuration"], release["root_causes"])
        self.assertIn("inventory_connection_errors", release["must_observe"])

    def test_orders_waits_for_inventory_service_and_bad_release_remains_explicit_fault(self) -> None:
        compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        orders = compose.split("\n  orders:\n    build:", 1)[1].split("\n  inventory:\n    build:", 1)[0]
        inventory = compose.split("\n  inventory:\n    build:", 1)[1].split("\n  payment:\n    build:", 1)[0]
        self.assertIn("inventory:\n        condition: service_started", orders)
        self.assertIn("networks: [backend]", orders)
        self.assertIn("networks: [backend]", inventory)
        self.assertIn("INVENTORY_URL: ${LAB_INVENTORY_URL:-http://inventory:8080}", orders)

        runner = (ROOT / "lab" / "run-scenario.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('if ($Scenario -eq "bad_release") { "http://inventory-v2:8080" } else { "http://inventory:8080" }', runner)
        self.assertLess(runner.index('$env:LAB_INVENTORY_URL ='), runner.index('docker compose @composeFiles up'))
        self.assertIn('SERVICE_VERSION: ${LAB_ORDERS_VERSION:-v1}', orders)
        self.assertIn('$env:LAB_ORDERS_VERSION = if ($Scenario -eq "bad_release") { "v2" } else { "v1" }', runner)
        self.assertIn('$scenarioBuildFlag = if ($Scenario -eq "bad_release") { "--no-build" } else { "--build" }', runner)
        self.assertIn('Healthy baseline checkout failed before bad_release injection', runner)
        self.assertLess(runner.index('Healthy baseline checkout failed before bad_release injection'),
            runner.index('$env:LAB_FAULT = $Scenario'))
        self.assertLess(runner.index('Could not reset deterministic lab state after baseline'),
            runner.index('$env:LAB_FAULT = $Scenario'))

    def test_smoke_expected_http_outcomes_cover_all_twelve_faults(self) -> None:
        truth = json.loads((ROOT / "lab" / "scenarios" / "truth.json").read_text(encoding="utf-8"))
        smoke = (ROOT / "lab" / "smoke.ps1").read_text(encoding="utf-8-sig")
        match = re.search(r'\$expectedFailure = \$case\.fault -in @\(([^)]*)\)', smoke)
        self.assertIsNotNone(match)
        expected_failures = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        actual_faults = {case["fault"] for case in truth["cases"]}
        self.assertEqual({"db_pool_exhaustion", "dependency_timeout", "bad_release", "double_root", "payment_reject", "redis_unavailable"}, expected_failures)
        self.assertEqual({"slow_sql", "retry_storm", "inventory_latency", "stale_inventory", "duplicate_delivery", "inventory_depleted"}, actual_faults - expected_failures)
        self.assertIn('$windowStart = [DateTime]::UtcNow.ToString("o")', smoke)
        self.assertIn('18081/logs?start=$from&end=$to', smoke)
        self.assertIn('18080/deployments?start=$from&end=$to', smoke)
        self.assertNotIn("AddMinutes(-5)", smoke)

    def test_gateway_wait_is_bounded_long_enough_for_downstream_dns_failure(self) -> None:
        source = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
        self.assertIn("ORDERS_REQUEST_TIMEOUT_SECONDS = 10.0", source)
        self.assertIn("timeout=ORDERS_REQUEST_TIMEOUT_SECONDS", source)
        self.assertIn('emit("error", "inventory dependency failed", error_type=type(exc).__name__', source)
        self.assertIn('dependency_host=urlsplit(INVENTORY_URL).hostname', source)
        self.assertIn('error_reason=str(reason)[:160]', source)
        investigator = (ROOT / "opspilot" / "live_investigation.py").read_text(encoding="utf-8")
        self.assertIn('attrs.get("dependency_host") == "inventory-v2"', investigator)

    def test_inventory_rejection_and_trace_spans_keep_order_correlation(self) -> None:
        source = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
        self.assertIn('span.set_attribute("opspilot.order_id", str(payload["order_id"]))', source)
        self.assertIn('span.set_attribute("opspilot.order_id", order_id)', source)
        self.assertIn('emit("warning", "inventory reservation rejected", order_id=order_id', source)
        self.assertIn('emit("warning", "payment attempt failed", attempt=attempt + 1, error_type=type(exc).__name__, order_id=order_id)', source)

    def test_inventory_faults_receive_the_selected_fault_environment(self) -> None:
        compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        inventory = compose.split("  inventory:\n", 1)[1].split("  payment:\n", 1)[0]
        self.assertIn("LAB_FAULT: ${LAB_FAULT:-none}", inventory)
        matrix = (ROOT / "lab" / "run-host-matrix.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$windowSpans.Count -gt 0 -and $missingTraceSignals.Count -eq 0", matrix)

    def test_inventory_latency_fault_timeout_allows_injected_delay_to_complete(self) -> None:
        source = (ROOT / "lab" / "service" / "app.py").read_text(encoding="utf-8")
        self.assertIn('inventory_timeout = 1.5 if FAULT == "inventory_latency" else 0.5', source)
        self.assertIn('time.sleep(0.8)', source)
        contract = (ROOT / "lab" / "op1-matrix-contract.ps1").read_text(encoding="utf-8")
        self.assertIn('"orders_latency" { $matches = @($spans | Where-Object { $_.service -eq "orders" -and $_.operation -match "orders" -and $_.duration -ge 750000 }) }', contract)


if __name__ == "__main__":
    unittest.main()
