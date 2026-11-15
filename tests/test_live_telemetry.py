from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from opspilot.live_investigation import investigate_live
from opspilot.telemetry import LabTelemetry


START = "2026-10-23T10:00:00Z"
END = "2026-10-23T10:10:00Z"


class FakeTelemetry:
    def __init__(self, results: dict[str, dict]) -> None:
        self.results = results

    def __getattr__(self, source: str):
        if source not in {"metrics", "logs", "traces", "deployments"}:
            raise AttributeError(source)
        return lambda *_args: self.results[source]


def record(source: str, record_id: str, timestamp: str, *, service: str | None = None,
           trace_id: str | None = None, attributes: dict | None = None, raw: dict | None = None) -> dict:
    return {"source": source, "record_id": record_id, "timestamp": timestamp, "service": service,
        "trace_id": trace_id, "attributes": attributes or {}, "raw": raw or {}}


def bad_release_snapshot(*, span_overrides: dict | None = None,
                         deployment_timestamp: str = "2026-10-23T10:04:00Z",
                         log_timestamp: str = "2026-10-23T10:06:22Z") -> dict[str, dict]:
    trace_id = "8156b9110369c9f55c6848435ca6bc40"
    order_id = "synthetic-bad-release-001"
    deployment = record("deployments", "deploy-orders-v2", deployment_timestamp,
        service="orders", attributes={"version": "v2"})
    error = record("logs", "orders-inventory-dns-error", log_timestamp,
        service="orders", trace_id=trace_id,
        raw={"severity": "error", "service_version": "v2", "order_id": order_id,
            "trace_id": trace_id, "message": "inventory dependency failed", "attributes": {
                "error_type": "URLError", "dependency_host": "inventory-v2",
                "error_reason": "No address associated with hostname"}})
    gateway_span = record("traces", "gateway-checkout-span", "2026-10-23T10:06:22Z",
        service="gateway", trace_id=trace_id, attributes={"span_id": "gateway-span",
            "parent_span_id": None, "operation": "gateway/checkout", "attributes": {
                "service.version": "v1", "opspilot.order_id": order_id,
                "http.response.status_code": 502}})
    orders_span = record("traces", "orders-v2-error-span", "2026-10-23T10:06:22.200Z",
        service="orders", trace_id=trace_id, attributes={"span_id": "orders-span",
            "parent_span_id": "gateway-span", "operation": "orders/orders", "attributes": {
                "service.version": "v2", "opspilot.order_id": order_id,
                "http.response.status_code": 500}})
    if span_overrides:
        orders_span.update(span_overrides)
    empty = {"records": []}
    return {"metrics": empty, "logs": {"records": [error]},
        "traces": {"records": [gateway_span, orders_span]},
        "deployments": {"records": [deployment]}}


class LiveTelemetryTests(unittest.TestCase):
    def test_rejects_non_loopback_endpoints_and_unbounded_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            LabTelemetry(prometheus="http://example.com:9090")
        adapter = LabTelemetry()
        with self.assertRaisesRegex(ValueError, "24 hours"):
            adapter.logs("2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z", 100)
        with self.assertRaisesRegex(ValueError, "limit"):
            adapter.logs(START, END, 1001)

    def test_prometheus_query_is_get_bounded_and_returns_stable_source_refs(self) -> None:
        inside_start = datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() + 60
        outside_start = datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() - 2
        exact_end = datetime.fromisoformat(END.replace("Z", "+00:00")).timestamp()
        payload = {"status": "success", "data": {"result": [{
            "metric": {"__name__": "opspilot_http_requests_total", "service": "orders", "version": "v1"},
            "values": [[outside_start, "1"], [inside_start, "3"], [exact_end, "4"]],
        }]}}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        adapter = LabTelemetry()
        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response()]) as opened:
            result = adapter.metrics(START, END, 10)
            repeated = adapter.metrics(START, END, 10)
        request = opened.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["source"], "metrics")
        self.assertTrue(result["records"][0]["record_id"].startswith("metrics:"))
        self.assertEqual([row["record_id"] for row in result["records"]], [row["record_id"] for row in repeated["records"]])
        self.assertEqual(result["records"][0]["attributes"]["value"], "3")

    def test_metrics_filters_window_then_applies_global_limit_with_stable_refs(self) -> None:
        lower = datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp()
        upper = datetime.fromisoformat(END.replace("Z", "+00:00")).timestamp()
        payload = {"status": "success", "data": {"result": [
            {"metric": {"__name__": "opspilot_http_errors_total", "service": "orders", "version": "v1"},
             "values": [[lower - 1, "0"], [lower + 60, "1"]]},
            {"metric": {"__name__": "opspilot_http_errors_total", "service": "payment", "version": "v1"},
             "values": [[lower + 120, "2"], [upper + 1, "3"]]},
        ]}}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response()]):
            first = LabTelemetry().metrics(START, END, 1)
            repeated = LabTelemetry().metrics(START, END, 1)
        self.assertEqual(1, len(first["records"]))
        self.assertEqual("1", first["records"][0]["attributes"]["value"])
        self.assertEqual(first["records"][0]["record_id"], repeated["records"][0]["record_id"])
        self.assertTrue(first["records"][0]["record_id"].startswith("metrics:"))

    def test_log_adapter_enforces_global_limit_across_services(self) -> None:
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(self.payload).encode()

        row = {"timestamp": "2026-10-23T10:05:00Z", "message": "request completed", "trace_id": "abc"}
        adapter = LabTelemetry()
        with patch("opspilot.telemetry.urlopen", side_effect=[Response({"records": [row] * 2}) for _ in range(4)]) as opened:
            result = adapter.logs(START, END, 4)
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(opened.call_count, 4)
        self.assertTrue(all(call.args[0].method == "GET" for call in opened.call_args_list))
        self.assertTrue(any("127.0.0.1:18080/logs?" in call.args[0].full_url for call in opened.call_args_list))

    def test_log_adapter_keeps_healthy_services_when_one_endpoint_fails(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps({"records": [
                {"timestamp": "2026-10-23T10:05:00Z", "message": "request completed"}]}).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response(), URLError("offline"), Response()]):
            result = LabTelemetry().logs(START, END, 10)
        self.assertEqual({row["service"] for row in result["records"]}, {"gateway", "orders", "payment"})
        self.assertEqual(result["failed_services"], {"inventory": "URLError"})
        self.assertIn("partial_service_unavailable", result["warnings"])

    def test_log_record_ids_include_source_service(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps({"records": [
                {"timestamp": "2026-10-23T10:05:00Z", "message": "same event"}]}).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response(), Response(), Response()]):
            result = LabTelemetry().logs(START, END, 4)
        self.assertEqual({row["service"] for row in result["records"]}, {"gateway", "orders", "inventory", "payment"})
        self.assertEqual(len({row["record_id"] for row in result["records"]}), 4)

    def test_log_adapter_rejects_records_outside_requested_window(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps({"records": [
                {"timestamp": "2026-10-23T09:59:59Z", "message": "early"},
                {"timestamp": "2026-10-23T10:05:00Z", "message": "inside"},
                {"timestamp": END, "message": "at-end"},
                {"timestamp": "2026-10-23T10:10:01Z", "message": "late"},
                {"timestamp": "not-a-date", "message": "invalid"},
            ]}).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response(), Response(), Response()]):
            result = LabTelemetry().logs(START, END, 10)
        self.assertEqual({row["raw"]["message"] for row in result["records"]}, {"inside"})

    def test_log_adapter_filters_window_before_applying_global_limit(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps({"records": [
                {"timestamp": "2026-10-23T09:59:59Z", "message": "outside"},
                {"timestamp": "2026-10-23T10:05:00Z", "message": "inside"}]}).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response(), Response(), Response()]):
            result = LabTelemetry().logs(START, END, 1)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["raw"]["message"], "inside")
        self.assertTrue(result["records"][0]["record_id"].startswith("logs:"))

    def test_jaeger_span_mapping_preserves_parent_and_deduplicates_trace_queries(self) -> None:
        lower = datetime.fromisoformat(START.replace("Z", "+00:00"))
        start_us = int(lower.timestamp() * 1_000_000) + 1_000_000
        payload = {"data": [{"traceID": "a" * 32, "processes": {
                "gw": {"serviceName": "gateway", "tags": [{"key": "service.version", "value": "v1"}]},
                "ord": {"serviceName": "orders", "tags": [{"key": "service.version", "value": "v2"}]},
            }, "spans": [
                {"spanID": "g1", "processID": "gw", "operationName": "gateway/checkout", "startTime": start_us,
                    "duration": 40000, "tags": [{"key": "http.status_code", "value": 200}], "references": []},
                {"spanID": "o1", "processID": "ord", "operationName": "orders/orders", "startTime": start_us + 1000,
                    "duration": 30000, "tags": [], "references": [{"refType": "CHILD_OF", "spanID": "g1"}]},
            ]}]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response() for _ in range(4)]) as opened:
            result = LabTelemetry().traces(START, END, 10)
        self.assertEqual(opened.call_count, 4)
        self.assertTrue(all(call.args[0].method == "GET" for call in opened.call_args_list))
        self.assertEqual(len(result["records"]), 2)
        orders = next(row for row in result["records"] if row["service"] == "orders")
        self.assertEqual(orders["attributes"]["span_id"], "o1")
        self.assertEqual(orders["attributes"]["parent_span_id"], "g1")
        self.assertEqual(orders["attributes"]["duration_us"], 30000)

    def test_jaeger_keeps_valid_spans_when_one_service_fails_and_drops_malformed_span(self) -> None:
        start_us = int(datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() * 1_000_000) + 1_000_000
        payload = {"data": [{"traceID": "c" * 32, "processes": {"orders": {"serviceName": "orders"}},
            "spans": [{"spanID": "valid", "processID": "orders", "operationName": "orders/orders",
                "startTime": start_us, "duration": 10, "tags": [], "references": []},
                {"spanID": "bad", "processID": "orders", "operationName": "broken", "tags": []}]}]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), URLError("offline"), Response(), Response()]) as opened:
            result = LabTelemetry().traces(START, END, 10)
        self.assertEqual(opened.call_count, 4)
        self.assertEqual([row["attributes"]["span_id"] for row in result["records"]], ["valid"])
        self.assertEqual(result["failed_services"], {"orders": "URLError"})
        self.assertIn("partial_service_unavailable", result["warnings"])
        self.assertTrue(result["records"][0]["record_id"].startswith("traces:"))

    def test_jaeger_drops_spans_without_source_identity_and_investigation_abstains(self) -> None:
        start_us = int(datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() * 1_000_000)
        payload = {"data": [
            {"traceID": "", "processes": {"orders": {"serviceName": "orders"}}, "spans": [
                {"spanID": "span-missing-trace", "processID": "orders", "operationName": "postgres.query",
                 "startTime": start_us + 1_000_000, "duration": 2_000_000}]},
            {"traceID": "a" * 32, "processes": {"orders": {"serviceName": "orders"}}, "spans": [
                {"processID": "orders", "operationName": "postgres.query",
                 "startTime": start_us + 1_000_000, "duration": 2_000_000}]},
        ]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response() for _ in range(4)]):
            traces = LabTelemetry().traces(START, END, 10)
        self.assertEqual([], traces["records"])
        empty = {"records": []}
        report = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": empty, "traces": traces, "deployments": empty,
        }))
        self.assertEqual("abstain", report["confidence"])
        self.assertEqual([], report["root_causes"])
        self.assertEqual([], report["evidence_refs"])

    def test_jaeger_query_uses_window_and_caps_global_result_after_filtering(self) -> None:
        lower = int(datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() * 1_000_000)
        payload = {"data": [{"traceID": "d" * 32, "processes": {"orders": {"serviceName": "orders"}},
            "spans": [{"spanID": "outside", "processID": "orders", "operationName": "old",
                "startTime": lower - 1_000_000, "duration": 10},
                {"spanID": "inside", "processID": "orders", "operationName": "current",
                    "startTime": lower + 1_000_000, "duration": 10},
                {"spanID": "late", "processID": "orders", "operationName": "future",
                    "startTime": lower + 601_000_000, "duration": 10}]}]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response() for _ in range(4)]) as opened:
            result = LabTelemetry().traces(START, END, 1)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["attributes"]["span_id"], "inside")
        self.assertTrue(result["records"][0]["record_id"].startswith("traces:"))
        for call in opened.call_args_list:
            params = parse_qs(urlsplit(call.args[0].full_url).query)
            self.assertEqual(params["start"], [str(lower)])
            self.assertEqual(params["end"], [str(lower + 600_000_000)])
            self.assertEqual(params["limit"], ["1"])

    def test_pool_claim_needs_cross_source_evidence_and_cites_every_record(self) -> None:
        metrics = [
            record("metrics", "m0", "2026-10-23T10:01:00Z", service="orders", attributes={"name": "opspilot_http_errors_total", "version": "v1", "value": 0}),
            record("metrics", "m1", "2026-10-23T10:09:00Z", service="orders", attributes={"name": "opspilot_http_errors_total", "version": "v1", "value": 2}),
        ]
        logs = [record("logs", "l1", "2026-10-23T10:05:00Z", service="orders", raw={"message": "database sessions remain occupied", "attributes": {}, "timestamp": "2026-10-23T10:05:00Z"})]
        empty = {"records": []}
        result = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": {"records": metrics}, "logs": {"records": logs}, "traces": empty, "deployments": empty,
        }))
        self.assertEqual(result["root_causes"], ["database_pool_exhaustion"])
        refs = {(item["source"], item["record_id"]) for item in result["evidence_refs"]}
        cited = {(item["source"], item["record_id"]) for item in result["hypotheses"][0]["support"]}
        self.assertTrue(cited <= refs)
        self.assertIn("missing_trace_context", result["quality"]["warnings"])

    def test_counterevidence_forces_abstention(self) -> None:
        logs = [record("logs", "l1", "2026-10-23T10:05:00Z", service="orders", raw={"message": "database sessions remain occupied", "attributes": {}, "timestamp": "2026-10-23T10:05:00Z"})]
        metrics = [record("metrics", "m0", "2026-10-23T10:01:00Z", service="orders", attributes={"name": "opspilot_http_errors_total", "version": "v1", "value": 4}),
                   record("metrics", "m1", "2026-10-23T10:09:00Z", service="orders", attributes={"name": "opspilot_http_errors_total", "version": "v1", "value": 4})]
        empty = {"records": []}
        result = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": {"records": metrics}, "logs": {"records": logs}, "traces": empty, "deployments": empty,
        }))
        self.assertEqual(result["status"], "insufficient_evidence")
        pool = next(row for row in result["hypotheses"] if row["id"] == "database_pool_exhaustion")
        self.assertTrue(pool["against"])

    def test_timeout_and_retry_signals_remain_separate(self) -> None:
        trace_id = "a" * 32
        timeout_logs = [record("logs", "timeout-log", "2026-10-23T10:05:00Z", service="orders", trace_id=trace_id,
            raw={"message": "payment attempt failed", "trace_id": trace_id,
                "attributes": {"error_type": "TimeoutError"}, "timestamp": "2026-10-23T10:05:00Z"})]
        order_span = record("traces", "orders-span", "2026-10-23T10:05:00Z", service="orders", trace_id=trace_id,
            attributes={"operation": "orders/orders", "span_id": "orders-1", "parent_span_id": "gateway-1"})
        long_span = record("traces", "timeout-span", "2026-10-23T10:05:01Z", service="payment", trace_id=trace_id,
            attributes={"operation": "payment/charge", "span_id": "payment-1", "parent_span_id": "orders-1",
                "duration_us": 2_000_000})
        empty = {"records": []}
        timeout = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": {"records": timeout_logs}, "traces": {"records": [order_span, long_span]}, "deployments": empty,
        }))
        self.assertEqual(timeout["root_causes"], ["payment_dependency_timeout"])
        linked = next(row for row in timeout["hypotheses"] if row["id"] == "payment_dependency_timeout")
        self.assertEqual("causal_trace_link", linked["evidence_relationship"])
        self.assertEqual(["timeout-log", "timeout-span"], sorted(ref["record_id"] for ref in linked["causal_links"][0]["evidence"]))

        unlinked_log = record("logs", "unrelated-timeout", "2026-10-23T10:05:00Z", service="orders",
            trace_id="b" * 32, raw={"message": "payment attempt failed", "trace_id": "b" * 32,
                "attributes": {"error_type": "TimeoutError"}, "timestamp": "2026-10-23T10:05:00Z"})
        unlinked = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": {"records": [unlinked_log]},
            "traces": {"records": [order_span, long_span]}, "deployments": empty,
        }))
        self.assertNotIn("payment_dependency_timeout", unlinked["root_causes"])
        unlinked_hypothesis = next(row for row in unlinked["hypotheses"] if row["id"] == "payment_dependency_timeout")
        self.assertEqual("cross_source_correlation_only", unlinked_hypothesis["evidence_relationship"])
        self.assertIn("unlinked_cross_source_signals", unlinked["quality"]["warnings"])

        drifted_log = dict(timeout_logs[0], timestamp="2026-10-23T10:05:20Z")
        drifted = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": {"records": [drifted_log]},
            "traces": {"records": [order_span, long_span]}, "deployments": empty,
        }))
        self.assertNotIn("payment_dependency_timeout", drifted["root_causes"])
        drifted_hypothesis = next(row for row in drifted["hypotheses"] if row["id"] == "payment_dependency_timeout")
        self.assertEqual("cross_source_correlation_only", drifted_hypothesis["evidence_relationship"])
        self.assertIn("source_clock_drift", drifted["quality"]["warnings"])

        retry_logs = [record("logs", f"retry-log-{i}", f"2026-10-23T10:05:0{i}Z", service="orders", trace_id=trace_id,
            raw={"message": "payment attempt failed", "trace_id": trace_id,
                "attributes": {"attempt": i + 1, "error_type": "HTTPError"}, "timestamp": f"2026-10-23T10:05:0{i}Z"}) for i in range(3)]
        retry_spans = [record("traces", f"retry-span-{i}", f"2026-10-23T10:05:0{i}Z", service="payment", trace_id=trace_id,
            attributes={"operation": "payment/charge", "duration_us": 20_000}) for i in range(4)]
        retry = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": {"records": retry_logs}, "traces": {"records": retry_spans}, "deployments": empty,
        }))
        self.assertEqual(retry["root_causes"], ["retry_amplification"])

    def test_release_and_slow_query_claims_need_cross_source_evidence(self) -> None:
        empty = {"records": []}
        release = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": {"records": [
                record("metrics", "release-error-before", "2026-10-23T10:01:00Z", service="orders",
                    attributes={"name": "opspilot_http_errors_total", "version": "v2", "value": 0}),
                record("metrics", "release-error-after", "2026-10-23T10:09:00Z", service="orders",
                    attributes={"name": "opspilot_http_errors_total", "version": "v2", "value": 2})]},
            "logs": empty, "traces": empty,
            "deployments": {"records": [record("deployments", "release", "2026-10-23T10:04:00Z",
                service="orders", attributes={"version": "v2"})]},
        }))
        self.assertEqual(release["root_causes"], ["invalid_release_configuration"])

    def test_bad_release_live_evidence_links_deployment_version_and_dns_failure_log(self) -> None:
        release = investigate_live(START, END, telemetry=FakeTelemetry(bad_release_snapshot()))
        self.assertEqual(release["root_causes"], ["invalid_release_configuration"])
        self.assertEqual({"start": START, "end": END}, release["window"])
        self.assertTrue(all(details["window"] == {"start": START, "end": END}
            for details in release["sources"].values()))
        hypothesis = next(item for item in release["hypotheses"]
            if item["id"] == "invalid_release_configuration")
        self.assertEqual("deployment_version_log_trace_correlation", hypothesis["evidence_relationship"])
        self.assertEqual({"deploy-orders-v2", "orders-inventory-dns-error", "orders-v2-error-span"},
            {item["record_id"] for item in hypothesis["support"]})
        self.assertEqual({"deployment_precedes_same_version_error", "same_request_orders_span"},
            {item["type"] for item in hypothesis["evidence_links"]})
        self.assertEqual([], hypothesis["evidence_gaps"])
        self.assertIn({"caller": "gateway", "callee": "orders",
            "evidence": [{"source": "traces", "record_id": "orders-v2-error-span"}]},
            release["dependency_graph"])

    def test_bad_release_trace_mismatches_are_reported_as_gaps_not_links(self) -> None:
        empty = {"records": []}
        cases = {
            "different trace": {"trace_id": "different-trace"},
            "different order": {"attributes": {"span_id": "orders-span", "parent_span_id": "gateway-span",
                "operation": "orders/orders", "attributes": {"service.version": "v2",
                "opspilot.order_id": "other-order", "http.response.status_code": 500}}},
            "different service": {"service": "inventory"},
            "different version": {"attributes": {"span_id": "orders-span", "parent_span_id": "gateway-span",
                "operation": "orders/orders", "attributes": {"service.version": "v1",
                "opspilot.order_id": "synthetic-bad-release-001", "http.response.status_code": 500}}},
            "outside incident window": {"timestamp": END},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                report = investigate_live(START, END,
                    telemetry=FakeTelemetry(bad_release_snapshot(span_overrides=overrides)))
                hypothesis = next(item for item in report["hypotheses"]
                    if item["id"] == "invalid_release_configuration")
                self.assertEqual("deployment_version_log_time_correlation_trace_unlinked",
                    hypothesis["evidence_relationship"])
                self.assertFalse(any(link["type"] == "same_request_orders_span"
                    for link in hypothesis["evidence_links"]))
                self.assertEqual("unlinked_request_trace", hypothesis["evidence_gaps"][0]["type"])

        no_trace_sources = bad_release_snapshot()
        no_trace_sources["traces"] = {"records": []}
        no_trace = investigate_live(START, END, telemetry=FakeTelemetry(no_trace_sources))
        no_trace_release = next(item for item in no_trace["hypotheses"]
            if item["id"] == "invalid_release_configuration")
        self.assertEqual("deployment_version_log_time_correlation_trace_unlinked",
            no_trace_release["evidence_relationship"])
        self.assertEqual("unlinked_request_trace", no_trace_release["evidence_gaps"][0]["type"])

        outside = investigate_live(START, END, telemetry=FakeTelemetry(bad_release_snapshot(
            deployment_timestamp="2026-10-23T09:59:59Z")))
        self.assertEqual("empty", outside["sources"]["deployments"]["status"])
        self.assertIn("records_outside_requested_window_dropped", outside["quality"]["warnings"])
        outside_release = next(item for item in outside["hypotheses"]
            if item["id"] == "invalid_release_configuration")
        self.assertEqual("insufficient_release_linkage", outside_release["evidence_relationship"])

    def test_structured_dns_log_and_slow_query_claims_need_cross_source_evidence(self) -> None:
        empty = {"records": []}
        structured = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty,
            "logs": {"records": [record("logs", "dns-structured", "2026-10-23T10:06:22Z", service="orders",
                attributes={"error_type": "URLError", "dependency_host": "inventory-v2",
                    "error_reason": "No address associated with hostname"}, raw={"service_version": "v2",
                    "severity": "error", "message": "inventory dependency failed",
                    "error_type": "URLError", "dependency_host": "inventory-v2",
                    "error_reason": "No address associated with hostname"})]},
            "traces": empty,
            "deployments": {"records": [record("deployments", "release-v2", "2026-10-23T10:04:23Z",
                service="orders", attributes={"version": "v2"})]},
        }))
        hypothesis = next(item for item in structured["hypotheses"] if item["id"] == "invalid_release_configuration")
        self.assertTrue(any(item["record_id"] == "dns-structured" for item in hypothesis["support"]))

        slow = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": {"records": [
                record("metrics", "duration-before", "2026-10-23T10:01:00Z", service="orders",
                    attributes={"name": "opspilot_http_duration_ms_sum", "version": "v1", "value": 0}),
                record("metrics", "duration-after", "2026-10-23T10:09:00Z", service="orders",
                    attributes={"name": "opspilot_http_duration_ms_sum", "version": "v1", "value": 1800})]},
            "logs": empty,
            "traces": {"records": [record("traces", "slow-query", "2026-10-23T10:05:00Z",
                service="orders", trace_id="c" * 32, attributes={"operation": "postgres.query", "duration_us": 1_600_000})]},
            "deployments": empty,
        }))
        self.assertEqual(slow["root_causes"], ["slow_database_query"])

    def test_double_root_reports_both_independent_causes(self) -> None:
        trace_id = "d" * 32
        slow_query = record("traces", "slow-query", "2026-10-23T10:05:00Z", service="orders",
            trace_id=trace_id, attributes={"operation": "postgres.query", "duration_us": 1_600_000})
        order_span = record("traces", "orders-span", "2026-10-23T10:05:00Z", service="orders", trace_id=trace_id,
            attributes={"operation": "orders/orders", "span_id": "orders-1", "parent_span_id": "gateway-1"})
        payment_timeout = record("traces", "payment-timeout", "2026-10-23T10:05:00Z", service="payment",
            trace_id=trace_id, attributes={"operation": "payment/charge", "span_id": "payment-1",
                "parent_span_id": "orders-1", "duration_us": 2_000_000})
        timeout_log = record("logs", "timeout-log", "2026-10-23T10:05:00Z", service="orders", trace_id=trace_id,
            raw={"message": "payment attempt failed", "trace_id": trace_id,
                "attributes": {"error_type": "TimeoutError"}, "timestamp": "2026-10-23T10:05:00Z"})
        result = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": {"records": [
                record("metrics", "duration-before", "2026-10-23T10:01:00Z", service="orders",
                    attributes={"name": "opspilot_http_duration_ms_sum", "version": "v1", "value": 0}),
                record("metrics", "duration-after", "2026-10-23T10:09:00Z", service="orders",
                    attributes={"name": "opspilot_http_duration_ms_sum", "version": "v1", "value": 1800})]},
            "logs": {"records": [timeout_log]}, "traces": {"records": [order_span, slow_query, payment_timeout]},
            "deployments": {"records": []},
        }))
        self.assertEqual(set(result["root_causes"]), {"slow_database_query", "payment_dependency_timeout"})

    def test_trace_parent_spans_build_dependency_edges(self) -> None:
        trace_id = "b" * 32
        spans = [
            record("traces", "gateway-span", "2026-10-23T10:05:00Z", service="gateway", trace_id=trace_id,
                attributes={"span_id": "g1", "parent_span_id": None, "operation": "gateway/checkout", "duration_us": 20_000}),
            record("traces", "orders-span", "2026-10-23T10:05:00Z", service="orders", trace_id=trace_id,
                attributes={"span_id": "o1", "parent_span_id": "g1", "operation": "orders/orders", "duration_us": 20_000}),
        ]
        empty = {"records": []}
        result = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": empty, "traces": {"records": spans}, "deployments": empty,
        }))
        self.assertEqual(result["dependency_graph"], [{"caller": "gateway", "callee": "orders",
            "evidence": [{"source": "traces", "record_id": "orders-span"}]}])

    def test_deployment_adapter_flags_out_of_order_events(self) -> None:
        payload = {"records": [
            {"record_id": "late", "timestamp": "2026-10-23T10:08:00Z", "service": "orders", "version": "v2"},
            {"record_id": "early", "timestamp": "2026-10-23T10:02:00Z", "service": "orders", "version": "v1"},
        ]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", return_value=Response()) as opened:
            result = LabTelemetry().deployments(START, END, 10)
        params = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(params["start"], [START])
        self.assertEqual(params["end"], [END])
        self.assertIn("source_order_out_of_order", result["warnings"])
        self.assertEqual([row["record_id"] for row in result["records"]], ["early", "late"])

    def test_all_adapters_use_half_open_end_boundary(self) -> None:
        deployment_payload = {"records": [
            {"record_id": "inside", "timestamp": "2026-10-23T10:09:59Z", "service": "orders"},
            {"record_id": "at-end", "timestamp": END, "service": "orders"},
        ]}
        trace_start = int(datetime.fromisoformat(END.replace("Z", "+00:00")).timestamp() * 1_000_000)
        trace_payload = {"data": [{"traceID": "e" * 32, "processes": {"orders": {"serviceName": "orders"}},
            "spans": [{"spanID": "at-end", "processID": "orders", "operationName": "orders/orders",
                "startTime": trace_start, "duration": 10}]}]}

        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(self.payload).encode()

        logs_payload = {"records": [
            {"timestamp": "2026-10-23T10:09:59Z", "message": "inside"},
            {"timestamp": END, "message": "at-end"},
        ]}
        with patch("opspilot.telemetry.urlopen", side_effect=[
                Response({"status": "success", "data": {"result": [{"metric": {"__name__": "x", "service": "orders"},
                    "values": [[trace_start / 1_000_000 - 1, "1"], [trace_start / 1_000_000, "2"]]}]}}),
                *[Response(logs_payload) for _ in range(4)], Response(trace_payload),
                *[Response({"data": []}) for _ in range(3)], Response(deployment_payload)]):
            adapter = LabTelemetry()
            metrics = adapter.metrics(START, END, 10)
            logs = adapter.logs(START, END, 10)
            traces = adapter.traces(START, END, 10)
            deployments = adapter.deployments(START, END, 10)
        self.assertEqual(1, len(metrics["records"]))
        self.assertEqual(["inside"], [row["raw"]["message"] for row in logs["records"] if row["service"] == "gateway"])
        self.assertNotIn("at-end", [row["attributes"]["span_id"] for row in traces["records"]])
        self.assertEqual(["inside"], [row["record_id"] for row in deployments["records"]])

    def test_deployment_adapter_filters_window_before_applying_limit(self) -> None:
        payload = {"records": [
            {"record_id": "outside", "timestamp": "2026-10-23T09:59:59Z", "service": "orders"},
            {"record_id": "inside", "timestamp": "2026-10-23T10:05:00Z", "service": "orders"},
        ]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", return_value=Response()) as opened:
            result = LabTelemetry().deployments(START, END, 1)
        params = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(params["limit"], ["1"])
        self.assertEqual([row["record_id"] for row in result["records"]], ["inside"])

    def test_deployment_without_record_id_gets_stable_source_pointer(self) -> None:
        payload = {"records": [{"timestamp": "2026-10-23T10:05:00Z", "service": "orders", "version": "v2"}]}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(payload).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response()]):
            first = LabTelemetry().deployments(START, END, 10)["records"][0]
            repeated = LabTelemetry().deployments(START, END, 10)["records"][0]
        self.assertTrue(first["record_id"].startswith("deployments:"))
        self.assertEqual(first["record_id"], repeated["record_id"])
        self.assertEqual(first["raw"]["version"], "v2")

    def test_out_of_order_deployment_signals_warn_and_do_not_create_root_cause(self) -> None:
        empty = {"records": []}
        deployments = {"records": [
            record("deployments", "newer-v2", "2026-10-23T10:08:00Z", service="orders", attributes={"version": "v2"}),
            record("deployments", "older-v1", "2026-10-23T10:02:00Z", service="orders", attributes={"version": "v1"}),
        ], "warnings": ["source_order_out_of_order"]}
        report = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": empty, "traces": empty, "deployments": deployments,
        }))
        self.assertEqual([], report["root_causes"])
        self.assertEqual("abstain", report["confidence"])
        self.assertIn("deployment_events_out_of_order", report["quality"]["warnings"])
        self.assertEqual({"newer-v2", "older-v1"}, {
            ref["record_id"] for ref in report["sources"]["deployments"]["evidence_refs"]})


if __name__ == "__main__":
    unittest.main()
