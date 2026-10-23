from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
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
        payload = {"status": "success", "data": {"result": [{
            "metric": {"__name__": "opspilot_http_requests_total", "service": "orders", "version": "v1"},
            "values": [[outside_start, "1"], [inside_start, "3"]],
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

    def test_log_adapter_enforces_global_limit_across_services(self) -> None:
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps(self.payload).encode()

        row = {"timestamp": "2026-10-23T10:05:00Z", "message": "request completed", "trace_id": "abc"}
        adapter = LabTelemetry()
        with patch("opspilot.telemetry.urlopen", side_effect=[Response({"records": [row] * 2}) for _ in range(3)]) as opened:
            result = adapter.logs(START, END, 4)
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(opened.call_count, 3)
        self.assertTrue(all(call.args[0].method == "GET" for call in opened.call_args_list))

    def test_log_adapter_rejects_records_outside_requested_window(self) -> None:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return json.dumps({"records": [
                {"timestamp": "2026-10-23T09:59:59Z", "message": "early"},
                {"timestamp": "2026-10-23T10:05:00Z", "message": "inside"},
                {"timestamp": "2026-10-23T10:10:01Z", "message": "late"},
                {"timestamp": "not-a-date", "message": "invalid"},
            ]}).encode()

        with patch("opspilot.telemetry.urlopen", side_effect=[Response(), Response(), Response()]):
            result = LabTelemetry().logs(START, END, 10)
        self.assertEqual({row["raw"]["message"] for row in result["records"]}, {"inside"})

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
        long_span = record("traces", "timeout-span", "2026-10-23T10:05:00Z", service="payment", trace_id=trace_id,
            attributes={"operation": "payment/charge", "duration_us": 2_000_000})
        empty = {"records": []}
        timeout = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": empty, "logs": {"records": timeout_logs}, "traces": {"records": [long_span]}, "deployments": empty,
        }))
        self.assertEqual(timeout["root_causes"], ["payment_dependency_timeout"])

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
        payment_timeout = record("traces", "payment-timeout", "2026-10-23T10:05:00Z", service="payment",
            trace_id=trace_id, attributes={"operation": "payment/charge", "duration_us": 2_000_000})
        timeout_log = record("logs", "timeout-log", "2026-10-23T10:05:00Z", service="orders", trace_id=trace_id,
            raw={"message": "payment attempt failed", "trace_id": trace_id,
                "attributes": {"error_type": "TimeoutError"}, "timestamp": "2026-10-23T10:05:00Z"})
        result = investigate_live(START, END, telemetry=FakeTelemetry({
            "metrics": {"records": [
                record("metrics", "duration-before", "2026-10-23T10:01:00Z", service="orders",
                    attributes={"name": "opspilot_http_duration_ms_sum", "version": "v1", "value": 0}),
                record("metrics", "duration-after", "2026-10-23T10:09:00Z", service="orders",
                    attributes={"name": "opspilot_http_duration_ms_sum", "version": "v1", "value": 1800})]},
            "logs": {"records": [timeout_log]}, "traces": {"records": [slow_query, payment_timeout]},
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


if __name__ == "__main__":
    unittest.main()
