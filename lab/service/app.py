"""Small real HTTP services for the reproducible OpsPilot fault lab."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import uuid

import psycopg
import redis
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE = os.environ.get("SERVICE_NAME", "unknown")
VERSION = os.environ.get("SERVICE_VERSION", "v1")
FAULT = os.environ.get("LAB_FAULT", "none")
PORT = int(os.environ.get("PORT", "8080"))
OTLP = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
DEPLOYMENT_FILE = os.environ.get("DEPLOYMENT_FILE", "")
DB_HOST = os.environ.get("DB_HOST", "")
DB_NAME = os.environ.get("DB_NAME", "opspilot")
DB_USER = os.environ.get("DB_USER", "opspilot")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
REDIS_HOST = os.environ.get("REDIS_HOST", "")
INVENTORY_URL = os.environ.get("INVENTORY_URL", "http://inventory:8080")
PAYMENT_URL = os.environ.get("PAYMENT_URL", "http://payment:8080")
ORDERS_URL = os.environ.get("ORDERS_URL", "http://orders:8080")
LOCK = threading.Lock()
LOGS: deque[dict[str, Any]] = deque(maxlen=5000)
COUNTS: dict[str, int] = {"requests": 0, "errors": 0, "duration_ms_sum": 0}
POOL_FAULT_STARTED = False
PAYMENT_ATTEMPTS = 0
PAYMENT_FAILURES_REMAINING = 3

if OTLP:
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE, "service.version": VERSION}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{OTLP.rstrip('/')}/v1/traces"), schedule_delay_millis=500,
    ))
    trace.set_tracer_provider(provider)
TRACER = trace.get_tracer("opspilot.lab")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(level: str, message: str, **fields: Any) -> None:
    span = trace.get_current_span().get_span_context()
    item = {
        "timestamp": now(), "severity": level, "message": message,
        "service": SERVICE, "service_version": VERSION,
        "trace_id": format(span.trace_id, "032x") if span.is_valid else None,
        "span_id": format(span.span_id, "016x") if span.is_valid else None,
        **fields,
    }
    with LOCK:
        LOGS.append(item)
    print(json.dumps(item, ensure_ascii=False, separators=(",", ":")), flush=True)


def db_connect() -> psycopg.Connection:
    return psycopg.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, connect_timeout=1)


def json_call(url: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    carrier: dict[str, str] = {}
    from opentelemetry.propagate import inject
    inject(carrier)
    for key, value in carrier.items():
        request.add_header(key, value)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class ResponseError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def start_pool_pressure() -> None:
    global POOL_FAULT_STARTED
    with LOCK:
        if POOL_FAULT_STARTED:
            return
        POOL_FAULT_STARTED = True

    def hold_connections() -> None:
        connections: list[psycopg.Connection] = []
        try:
            for _ in range(18):
                connections.append(db_connect())
            emit("warning", "database sessions remain occupied", occupied_sessions=len(connections))
            time.sleep(8)
        except Exception as exc:
            emit("error", "database session pressure could not be established", error_type=type(exc).__name__)
        finally:
            for connection in connections:
                connection.close()
            emit("info", "database sessions released", released_sessions=len(connections))

    threading.Thread(target=hold_connections, name="lab-db-pressure", daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "OpsPilotLab/1.0"

    def log_message(self, *_: Any) -> None:
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("request body too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        if path.path == "/health":
            try:
                if DB_HOST:
                    with db_connect() as connection:
                        connection.execute("SELECT 1")
                if REDIS_HOST:
                    redis.Redis(host=REDIS_HOST, socket_connect_timeout=1).ping()
                self.send_json(200, {"status": "ok", "service": SERVICE, "version": VERSION})
            except Exception as exc:
                self.send_json(503, {"status": "degraded", "error_type": type(exc).__name__})
            return
        if path.path == "/metrics":
            with LOCK:
                snapshot = dict(COUNTS)
            body = (
                "# TYPE opspilot_http_requests_total counter\n"
                f'opspilot_http_requests_total{{service="{SERVICE}",version="{VERSION}"}} {snapshot["requests"]}\n'
                "# TYPE opspilot_http_errors_total counter\n"
                f'opspilot_http_errors_total{{service="{SERVICE}",version="{VERSION}"}} {snapshot["errors"]}\n'
                "# TYPE opspilot_http_duration_ms_sum counter\n"
                f'opspilot_http_duration_ms_sum{{service="{SERVICE}",version="{VERSION}"}} {snapshot["duration_ms_sum"]}\n'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.path == "/logs":
            query = parse_qs(path.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
                if not 1 <= limit <= 1000:
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "limit must be between 1 and 1000"})
                return
            start, end = query.get("start", [None])[0], query.get("end", [None])[0]
            with LOCK:
                rows = list(LOGS)
            rows = [row for row in rows if (start is None or row["timestamp"] >= start) and (end is None or row["timestamp"] <= end)]
            self.send_json(200, {"records": rows[-limit:], "count": min(limit, len(rows))})
            return
        if path.path == "/deployments" and SERVICE == "gateway":
            query = parse_qs(path.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
                if not 1 <= limit <= 1000:
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "limit must be between 1 and 1000"})
                return
            try:
                with open(DEPLOYMENT_FILE, encoding="utf-8") as stream:
                    rows = [json.loads(line) for line in stream if line.strip()]
            except FileNotFoundError:
                rows = []
            self.send_json(200, {"records": rows[-limit:], "count": min(limit, len(rows))})
            return
        if path.path == "/ready":
            self.send_json(200, {"ready": True, "service": SERVICE, "version": VERSION})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        started = time.perf_counter()
        status = 200
        try:
            payload = self.body_json()
            from opentelemetry.propagate import extract
            parent = extract({key.lower(): value for key, value in self.headers.items()})
            with TRACER.start_as_current_span(f"{SERVICE}{urlsplit(self.path).path}", context=parent) as span:
                span.set_attribute("service.name", SERVICE)
                span.set_attribute("service.version", VERSION)
                span.set_attribute("http.request.method", "POST")
                span.set_attribute("http.route", urlsplit(self.path).path)
                if SERVICE == "gateway" and urlsplit(self.path).path == "/checkout":
                    result = json_call(f"{ORDERS_URL}/orders", payload, timeout=3.0)
                elif SERVICE == "orders" and urlsplit(self.path).path == "/orders":
                    result = self.place_order(payload)
                elif SERVICE == "inventory" and urlsplit(self.path).path == "/reserve":
                    result = self.reserve_inventory(payload)
                elif SERVICE == "payment" and urlsplit(self.path).path == "/charge":
                    result = self.charge(payload)
                else:
                    status, result = 404, {"error": "not found"}
                span.set_attribute("http.response.status_code", status)
            self.send_json(status, result)
            emit("info", "request completed", path=urlsplit(self.path).path, status=status)
        except ResponseError as exc:
            status = exc.status
            with LOCK:
                COUNTS["errors"] += 1
            emit("warning", "request returned an error response", path=urlsplit(self.path).path, status=status)
            self.send_json(status, {"error": str(exc)})
        except (TimeoutError, URLError) as exc:
            status = 504 if isinstance(exc, TimeoutError) else 502
            with LOCK:
                COUNTS["errors"] += 1
            emit("error", "request dependency failed", path=urlsplit(self.path).path, error_type=type(exc).__name__)
            self.send_json(status, {"error": "dependency unavailable", "error_type": type(exc).__name__})
        except Exception as exc:
            status = 500
            with LOCK:
                COUNTS["errors"] += 1
            emit("error", "request failed", path=urlsplit(self.path).path, error_type=type(exc).__name__)
            self.send_json(status, {"error": "request failed", "error_type": type(exc).__name__})
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            with LOCK:
                COUNTS["requests"] += 1
                COUNTS["duration_ms_sum"] += elapsed

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        global PAYMENT_ATTEMPTS
        order_id = str(payload.get("order_id") or uuid.uuid4())
        if FAULT == "db_pool_exhaustion":
            start_pool_pressure()
        if FAULT in {"slow_sql", "double_root"}:
            with TRACER.start_as_current_span("postgres.query") as span:
                span.set_attribute("db.system", "postgresql")
                with db_connect() as connection:
                    connection.execute("SELECT pg_sleep(1.5)")
        if FAULT == "db_pool_exhaustion":
            time.sleep(0.25)
        with db_connect() as connection:
            connection.execute("INSERT INTO orders (order_id, sku, quantity) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (order_id, "demo-1", 1))
        if REDIS_HOST:
            redis.Redis(host=REDIS_HOST, socket_connect_timeout=1).set(f"order:{order_id}", "accepted", ex=3600)
        try:
            inventory = json_call(f"{INVENTORY_URL}/reserve", {"order_id": order_id}, timeout=0.5)
        except (TimeoutError, URLError) as exc:
            raise RuntimeError("inventory dependency failed") from exc
        retries = 4 if FAULT == "retry_storm" else 1
        payment: dict[str, Any] = {}
        for attempt in range(retries):
            PAYMENT_ATTEMPTS += 1
            try:
                payment = json_call(f"{PAYMENT_URL}/charge", {"order_id": order_id}, timeout=0.4 if FAULT in {"dependency_timeout", "double_root"} else 1.0)
                break
            except Exception as exc:
                emit("warning", "payment attempt failed", attempt=attempt + 1, error_type=type(exc).__name__)
                if attempt + 1 == retries:
                    raise RuntimeError("payment attempts exhausted")
        return {"order_id": order_id, "inventory": inventory.get("reserved", False), "payment": payment.get("charged", False)}

    def reserve_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        sku = str(payload.get("sku", "demo-1"))
        with db_connect() as connection:
            row = connection.execute("UPDATE inventory SET available=available-1 WHERE sku=%s AND available>0 RETURNING available", (sku,)).fetchone()
        return {"reserved": row is not None, "remaining": row[0] if row else 0}

    def charge(self, payload: dict[str, Any]) -> dict[str, Any]:
        global PAYMENT_FAILURES_REMAINING
        if FAULT in {"dependency_timeout", "double_root"}:
            time.sleep(2.0)
        if FAULT == "retry_storm":
            with LOCK:
                if PAYMENT_FAILURES_REMAINING > 0:
                    PAYMENT_FAILURES_REMAINING -= 1
                    raise ResponseError(503, "temporary payment failure")
        return {"charged": True, "order_id": payload.get("order_id")}


if __name__ == "__main__":
    emit("info", "service started", listen_port=PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
