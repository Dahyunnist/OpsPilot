from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "lab" / "service"


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        return self

    def fetchone(self):
        return (19,)


class _Redis:
    def __init__(self, **_kwargs):
        pass

    def ping(self):
        return True

    def set(self, *_args, **_kwargs):
        return True


class _SpanContext:
    is_valid = False
    trace_id = 0
    span_id = 0


class _Span:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def set_attribute(self, *_args):
        pass

    def get_span_context(self):
        return _SpanContext()


class _Tracer:
    def start_as_current_span(self, *_args, **_kwargs):
        return _Span()


class LabCheckoutE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.deployment_file = self.root / "deployments.jsonl"
        self.deployment_file.write_text(json.dumps({
            "record_id": "deploy-bad-release", "timestamp": self._iso(datetime.now(timezone.utc)),
            "service": "orders", "version": "v2",
        }) + "\n", encoding="utf-8")
        self.patcher = patch.dict(sys.modules, self._fake_modules())
        self.patcher.start()
        sys.path.insert(0, str(SERVICE_DIR))
        self.modules: dict[str, types.ModuleType] = {}
        for name in ("inventory", "payment", "orders", "gateway"):
            module = self._load_service(name)
            self.modules[name] = module
        inventory, payment, orders, gateway = (self.modules[name] for name in
            ("inventory", "payment", "orders", "gateway"))
        for module, service in ((inventory, "inventory"), (payment, "payment"),
                                (orders, "orders"), (gateway, "gateway")):
            module.SERVICE = service
            module.VERSION = "v1"
            module.FAULT = "none"
            module.DB_HOST = "fake-postgres"
            module.REDIS_HOST = "fake-redis"
            module.OTLP = ""
        orders.INVENTORY_URL = "http://inventory:8080"
        orders.PAYMENT_URL = "http://payment:8080"
        gateway.ORDERS_URL = "http://orders:8080"
        gateway.DEPLOYMENT_FILE = str(self.deployment_file)
        orders.json_call = lambda url, payload=None, timeout=1.0: self._dependency_call(url, payload)
        gateway.json_call = lambda url, payload=None, timeout=1.0: self._dependency_call(url, payload)

    def tearDown(self) -> None:
        if str(SERVICE_DIR) in sys.path:
            sys.path.remove(str(SERVICE_DIR))
        self.patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _fake_modules() -> dict[str, types.ModuleType]:
        psycopg = types.ModuleType("psycopg")
        psycopg.connect = lambda **_kwargs: _Connection()
        redis = types.ModuleType("redis")
        redis.Redis = _Redis
        otel = types.ModuleType("opentelemetry")
        otel.__path__ = []
        trace = types.ModuleType("opentelemetry.trace")
        trace.get_tracer = lambda *_args, **_kwargs: _Tracer()
        trace.get_current_span = lambda: _Span()
        trace.set_tracer_provider = lambda *_args, **_kwargs: None
        otel.trace = trace
        propagate = types.ModuleType("opentelemetry.propagate")
        propagate.inject = lambda _carrier: None
        propagate.extract = lambda _carrier: None
        exporter = types.ModuleType("opentelemetry.exporter")
        exporter.__path__ = []
        otlp = types.ModuleType("opentelemetry.exporter.otlp")
        otlp.__path__ = []
        otlp_proto = types.ModuleType("opentelemetry.exporter.otlp.proto")
        otlp_proto.__path__ = []
        otlp_http = types.ModuleType("opentelemetry.exporter.otlp.proto.http")
        otlp_http.__path__ = []
        exporter_module = types.ModuleType("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        exporter_module.OTLPSpanExporter = lambda **_kwargs: object()
        sdk = types.ModuleType("opentelemetry.sdk")
        sdk.__path__ = []
        resources = types.ModuleType("opentelemetry.sdk.resources")
        resources.Resource = types.SimpleNamespace(create=lambda _attributes: object())
        sdk_trace = types.ModuleType("opentelemetry.sdk.trace")
        sdk_trace.TracerProvider = type("TracerProvider", (), {"add_span_processor": lambda *_args: None})
        trace_export = types.ModuleType("opentelemetry.sdk.trace.export")
        trace_export.BatchSpanProcessor = lambda *_args, **_kwargs: object()
        return {"psycopg": psycopg, "redis": redis, "opentelemetry": otel,
                "opentelemetry.trace": trace, "opentelemetry.propagate": propagate,
                "opentelemetry.exporter": exporter, "opentelemetry.exporter.otlp": otlp,
                "opentelemetry.exporter.otlp.proto": otlp_proto,
                "opentelemetry.exporter.otlp.proto.http": otlp_http,
                "opentelemetry.exporter.otlp.proto.http.trace_exporter": exporter_module,
                "opentelemetry.sdk": sdk, "opentelemetry.sdk.resources": resources,
                "opentelemetry.sdk.trace": sdk_trace, "opentelemetry.sdk.trace.export": trace_export}

    def _load_service(self, name: str) -> types.ModuleType:
        path = SERVICE_DIR / "app.py"
        spec = importlib.util.spec_from_file_location(f"lab_service_{name}", path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        with patch.dict(os.environ, {"SERVICE_NAME": name, "OTEL_EXPORTER_OTLP_ENDPOINT": ""}, clear=False):
            spec.loader.exec_module(module)
        return module

    def _dispatch(self, module: types.ModuleType, method: str, path: str, body: dict | None = None):
        handler = module.Handler.__new__(module.Handler)
        data = json.dumps(body).encode("utf-8") if body is not None else b""
        handler.path = path
        handler.headers = {"Content-Length": str(len(data))}
        handler.rfile = BytesIO(data)
        handler.wfile = BytesIO()
        handler.status = 200
        handler.send_response = lambda status: setattr(handler, "status", status)
        handler.send_header = lambda *_args: None
        handler.end_headers = lambda: None
        getattr(module.Handler, f"do_{method}")(handler)
        return handler.status, json.loads(handler.wfile.getvalue())

    def _dependency_call(self, url: str, payload: dict | None):
        host = urlsplit(url).hostname
        if host == "inventory-v2":
            raise URLError("Name or service not known")
        module = {name: item for name, item in self.modules.items() if name in {"orders", "inventory", "payment"}}
        if host == "orders":
            target = module["orders"]
            path = "/orders"
        elif host in {"inventory", "payment"}:
            target = module[host]
            path = urlsplit(url).path
        else:
            raise URLError(f"unexpected synthetic service host: {host}")
        status, response = self._dispatch(target, "POST", path, payload)
        if status >= 400:
            raise URLError("synthetic downstream error")
        return response

    def test_normal_checkout_succeeds_and_bad_release_fails_with_queryable_evidence(self) -> None:
        gateway = self.modules["gateway"]
        orders = self.modules["orders"]
        with redirect_stdout(StringIO()):
            baseline_status, baseline = self._dispatch(gateway, "POST", "/checkout", {"order_id": "normal-001"})
        self.assertEqual(200, baseline_status)
        self.assertTrue(baseline["inventory"])
        self.assertTrue(baseline["payment"])

        start = datetime.now(timezone.utc) - timedelta(seconds=1)
        orders.FAULT = "bad_release"
        orders.VERSION = "v2"
        orders.INVENTORY_URL = "http://inventory-v2:8080"
        with redirect_stdout(StringIO()):
            fault_status, fault = self._dispatch(gateway, "POST", "/checkout", {"order_id": "bad-release-001"})
        self.assertIn(fault_status, {502, 504})
        self.assertIn("error", fault)

        end = datetime.now(timezone.utc) + timedelta(seconds=1)
        query = urlencode({"start": self._iso(start), "end": self._iso(end), "limit": "100"})
        logs_status, logs = self._dispatch(orders, "GET", f"/logs?{query}")
        deployments_status, deployments = self._dispatch(gateway, "GET", f"/deployments?{query}")
        self.assertEqual(200, logs_status)
        self.assertTrue(any(row.get("order_id") == "bad-release-001"
            and row.get("dependency_host") == "inventory-v2"
            and "Name or service not known" in row.get("error_reason", "") for row in logs["records"]))
        self.assertEqual(200, deployments_status)
        self.assertTrue(any(row.get("record_id") == "deploy-bad-release"
            and row.get("service") == "orders" and row.get("version") == "v2"
            for row in deployments["records"]))

    def test_compose_keeps_unknown_fault_hostname_out_of_dns_and_services_share_network(self) -> None:
        compose = (ROOT / "lab" / "compose.yaml").read_text(encoding="utf-8")
        runner = (ROOT / "lab" / "run-scenario.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("networks: [backend]", compose)
        self.assertNotIn("inventory-v2:", compose)
        self.assertIn("${LAB_INVENTORY_URL:-http://inventory:8080}", compose)
        self.assertIn('if ($Scenario -eq "bad_release") { "http://inventory-v2:8080" } else { "http://inventory:8080" }', runner)
        self.assertIn("condition: service_started", compose)


if __name__ == "__main__":
    unittest.main()
