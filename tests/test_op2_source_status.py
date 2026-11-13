from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import patch

from opspilot.live_investigation import investigate_live
from opspilot.telemetry import LabTelemetry


START = "2026-10-23T10:00:00Z"
END = "2026-10-23T10:10:00Z"


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _size): return self.payload


class TelemetryHTTPFake:
    """Fake only the HTTP transport; production adapters parse and filter responses."""
    def __init__(self, *, logs=None, logs_503=(), deployments=()) -> None:
        self.logs = logs or {}
        self.logs_503 = set(logs_503)
        self.deployments = list(deployments)
        self.calls = []
        self.base = "http://127.0.0.1:18080"
        self._patch = patch("opspilot.telemetry.urlopen", side_effect=self.open)

    def adapter(self) -> LabTelemetry:
        return LabTelemetry(prometheus=self.base + "/prometheus", jaeger=self.base + "/jaeger",
            gateway=self.base + "/gateway", services={name: self.base + "/" + name
                for name in ("orders", "inventory", "payment")})

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        path = urlsplit(request.full_url).path
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[1] == "logs":
            service = parts[0]
            if service in self.logs_503:
                raise HTTPError(request.full_url, 503, "synthetic unavailable", {}, None)
            return FakeResponse({"records": self.logs.get(service, [])})
        if path == "/gateway/deployments":
            return FakeResponse({"records": self.deployments})
        if path == "/prometheus/api/v1/query_range":
            return FakeResponse({"status": "success", "data": {"result": []}})
        if path == "/jaeger/api/traces":
            return FakeResponse({"data": []})
        raise AssertionError(f"unexpected fake HTTP route: {path}")

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *_args):
        self._patch.stop()


class Op2SourceStatusTests(unittest.TestCase):
    def investigate(self, fake):
        return investigate_live(START, END, telemetry=fake.adapter())

    def test_no_matching_logs_and_out_of_window_deployment_are_explicit_empty(self):
        outside_deployment = {"record_id": "old-v2", "timestamp": "2026-10-23T09:59:59Z",
            "service": "orders", "version": "v2"}
        with TelemetryHTTPFake(deployments=[outside_deployment]) as fake:
            report = self.investigate(fake)
        self.assertEqual("empty", report["source_status"]["logs"])
        self.assertEqual("no_matching_records", report["sources"]["logs"]["reason"])
        self.assertEqual({"start": START, "end": END}, report["sources"]["logs"]["window"])
        self.assertEqual([], report["sources"]["logs"]["evidence_refs"])
        self.assertEqual("empty", report["source_status"]["deployments"])
        self.assertEqual(0, report["sources"]["deployments"]["record_count"])
        self.assertEqual([], report["root_causes"])
        self.assertEqual("abstain", report["confidence"])
        self.assertTrue(fake.calls)
        self.assertTrue(all(request.method == "GET" for request, _timeout in fake.calls))
        for request, _timeout in fake.calls:
            path = urlsplit(request.full_url).path
            if path.endswith("/logs") or path.endswith("/deployments"):
                query = parse_qs(urlsplit(request.full_url).query)
                self.assertEqual([START], query["start"])
                self.assertEqual([END], query["end"])

    def test_all_log_adapters_503_are_error_not_empty(self):
        with TelemetryHTTPFake(logs_503=("gateway", "orders", "inventory", "payment")) as fake:
            report = self.investigate(fake)
        self.assertEqual("error", report["source_status"]["logs"])
        details = report["sources"]["logs"]
        self.assertEqual("adapter_unavailable", details["reason"])
        self.assertEqual({"gateway", "orders", "inventory", "payment"}, set(details["failed_services"]))
        self.assertEqual({"error"}, set(details["service_status"].values()))
        self.assertEqual("HTTPError", details["failed_services"]["orders"])
        self.assertEqual([], report["root_causes"])
        self.assertEqual("abstain", report["confidence"])

    def test_partial_503_with_zero_records_is_partial_not_empty_or_total_error(self):
        with TelemetryHTTPFake(logs_503=("orders",)) as fake:
            report = self.investigate(fake)
        self.assertEqual("partial", report["source_status"]["logs"])
        self.assertEqual(0, report["sources"]["logs"]["record_count"])
        self.assertEqual("error", report["sources"]["logs"]["service_status"]["orders"])
        self.assertEqual("ok", report["sources"]["logs"]["service_status"]["gateway"])
        self.assertIn("one_or_more_sources_unavailable", report["quality"]["warnings"])
        self.assertEqual([], report["root_causes"])
        self.assertEqual("abstain", report["confidence"])

    def test_conflicting_release_signals_keep_refs_and_force_abstention(self):
        logs = {"orders": [{"timestamp": "2026-10-23T10:02:00Z", "severity": "error",
            "message": "inventory dependency failed", "service": "orders", "service_version": "v2",
            "order_id": "order-1", "error_type": "URLError", "dependency_host": "inventory-v2"}]}
        deployments = [{"record_id": "deploy-v2", "timestamp": "2026-10-23T10:05:00Z",
            "service": "orders", "version": "v2"}]
        with TelemetryHTTPFake(logs=logs, deployments=deployments) as fake:
            report = self.investigate(fake)
        self.assertEqual([], report["root_causes"])
        self.assertEqual("abstain", report["confidence"])
        release = next(item for item in report["hypotheses"] if item["id"] == "invalid_release_configuration")
        self.assertTrue(release["support"])
        self.assertTrue(release["against"])
        cited = {(item["source"], item["record_id"]) for item in report["evidence_refs"]}
        self.assertIn(("deployments", "deploy-v2"), cited)
        self.assertTrue(any(source == "logs" for source, _record_id in cited))
        self.assertEqual("ok", report["sources"]["logs"]["status"])
        self.assertEqual({"start": START, "end": END}, report["sources"]["deployments"]["window"])

    def test_contradictory_deployment_order_is_cited_but_cannot_certify_release_cause(self):
        logs = {"orders": [{"timestamp": "2026-10-23T10:04:00Z", "severity": "error",
            "message": "inventory dependency failed", "service": "orders", "service_version": "v2",
            "error_type": "URLError", "dependency_host": "inventory-v2"}]}
        deployments = [
            {"record_id": "release-before-error", "timestamp": "2026-10-23T10:03:00Z",
             "service": "orders", "version": "v2"},
            {"record_id": "release-after-error", "timestamp": "2026-10-23T10:05:00Z",
             "service": "orders", "version": "v2"},
        ]
        with TelemetryHTTPFake(logs=logs, deployments=deployments) as fake:
            report = self.investigate(fake)
        release = next(item for item in report["hypotheses"] if item["id"] == "invalid_release_configuration")
        self.assertEqual("abstain", report["confidence"])
        self.assertNotIn("invalid_release_configuration", report["root_causes"])
        self.assertTrue(release["support"])
        self.assertTrue(release["against"])
        available = {(ref["source"], ref["record_id"]) for details in report["sources"].values()
                     for ref in details["evidence_refs"]}
        cited = {(ref["source"], ref["record_id"]) for ref in report["evidence_refs"]}
        self.assertLessEqual(cited, available)
        self.assertIn(("deployments", "release-before-error"), cited)
        self.assertIn(("deployments", "release-after-error"), cited)
        self.assertTrue(all(source == "logs" or record_id in {"release-before-error", "release-after-error"}
                            for source, record_id in cited))


if __name__ == "__main__":
    unittest.main()
