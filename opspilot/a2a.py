"""Small A2A v0.3 JSON-RPC boundary for the independent cross-examiner."""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class A2AError(RuntimeError):
    pass


class TaskStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.message_tasks: dict[str, str] = {}

    def create(self, message_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            existing = self.message_tasks.get(message_id)
            if existing:
                return self.tasks[existing], False
            task_id = str(uuid.uuid4())
            task = {
                "id": task_id,
                "contextId": payload.get("task_id", task_id),
                "status": {"state": "working"},
                "artifacts": [],
                "history": [{"messageId": message_id, "role": "user", "parts": [{"kind": "data", "data": payload}]}],
                "metadata": {"messageId": message_id},
            }
            self.tasks[task_id] = task
            self.message_tasks[message_id] = task_id
            return task, True

    def finish(self, task_id: str, value: dict[str, Any]) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task and task["status"]["state"] == "working":
                task["artifacts"] = [{"artifactId": str(uuid.uuid4()), "name": "cross-examination", "parts": [{"kind": "data", "data": value}]}]
                task["status"] = {"state": "completed"}

    def fail(self, task_id: str, reason: str) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task and task["status"]["state"] == "working":
                task["status"] = {"state": "failed", "message": {"role": "agent", "parts": [{"kind": "text", "text": reason}]}}

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task and task["status"]["state"] == "working":
                task["status"] = {"state": "canceled"}
            return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.tasks.get(task_id)
            return json.loads(json.dumps(task)) if task else None


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class A2AServer:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.handler = handler
        self.store = TaskStore()
        store = self.store
        callback = self.handler

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _send(self, body: dict[str, Any], status: int = 200) -> None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                if self.path != "/.well-known/agent-card.json":
                    self._send({"error": "not found"}, 404)
                    return
                self._send({
                    "name": "OpsPilot Cross-Examiner",
                    "description": "Finds contradictions and alternative incident causes from evidence packages.",
                    "url": f"http://127.0.0.1:{self.server.server_port}/",
                    "version": "0.1.0",
                    "protocolVersion": "0.3.0",
                    "preferredTransport": "JSONRPC",
                    "defaultInputModes": ["application/json"],
                    "defaultOutputModes": ["application/json"],
                    "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
                    "skills": [{"id": "cross-examination", "name": "Cross-examination", "description": "Challenge incident hypotheses with cited evidence.", "tags": ["incident-response", "evidence"]}],
                })

            def do_POST(self) -> None:
                if self.path != "/":
                    self._send({"error": "not found"}, 404)
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < content_length <= 1_000_000:
                        self._send(_error(None, -32600, "Invalid Request size"))
                        return
                    request = json.loads(self.rfile.read(content_length))
                except (ValueError, json.JSONDecodeError):
                    self._send(_error(None, -32700, "Parse error"))
                    return
                request_id = request.get("id")
                if request.get("jsonrpc") != "2.0" or not isinstance(request_id, (str, int)):
                    self._send(_error(request_id, -32600, "Invalid Request"))
                    return
                method, params = request.get("method"), request.get("params", {})
                if method == "message/send":
                    message = params.get("message", {})
                    parts = message.get("parts", [])
                    data = next((part.get("data") for part in parts if part.get("kind") == "data"), None)
                    message_id = message.get("messageId")
                    if message.get("role") != "user" or not message_id or not isinstance(data, dict):
                        self._send(_error(request_id, -32602, "message/send requires user DataPart and messageId"))
                        return
                    task, created = store.create(message_id, data)
                    if created:
                        threading.Thread(target=self._work, args=(task["id"], data), daemon=True).start()
                    self._send({"jsonrpc": "2.0", "id": request_id, "result": {"task": store.get(task["id"])}})
                elif method == "tasks/get":
                    task = store.get(params.get("id", ""))
                    if task is None:
                        self._send(_error(request_id, -32001, "Task not found"))
                    else:
                        self._send({"jsonrpc": "2.0", "id": request_id, "result": {"task": task}})
                elif method == "tasks/cancel":
                    task = store.cancel(params.get("id", ""))
                    if task is None:
                        self._send(_error(request_id, -32001, "Task not found"))
                    else:
                        self._send({"jsonrpc": "2.0", "id": request_id, "result": {"task": task}})
                else:
                    self._send(_error(request_id, -32601, "Method not found"))

            @staticmethod
            def _work(task_id: str, payload: dict[str, Any]) -> None:
                try:
                    store.finish(task_id, callback(payload))
                except Exception as exc:
                    store.fail(task_id, type(exc).__name__)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/"

    @property
    def card_url(self) -> str:
        return self.url + ".well-known/agent-card.json"

    def __enter__(self) -> "A2AServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class A2AClient:
    def __init__(self, card_url: str, timeout: float = 30.0, poll_interval: float = 0.1) -> None:
        self.card_url = card_url
        self.timeout = min(120.0, max(0.05, timeout))
        self.poll_interval = min(1.0, max(0.02, poll_interval))
        self.counter = 0
        self.retry_count = 0
        self.card = self._get_card()
        if self.card.get("protocolVersion") != "0.3.0" or "cross-examination" not in {skill.get("id") for skill in self.card.get("skills", [])}:
            raise A2AError("A2A Agent Card version or skill mismatch")

    def _get_card(self) -> dict[str, Any]:
        with urlopen(self.card_url, timeout=self.timeout) as response:
            return json.loads(response.read(100_000))

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.counter += 1
        body = json.dumps({"jsonrpc": "2.0", "id": str(self.counter), "method": method, "params": params}).encode("utf-8")
        request = Request(self.card["url"], data=body, headers={"Content-Type": "application/json"})
        result = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read(1_000_000))
                break
            except (URLError, TimeoutError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError) and exc.code < 500:
                    break
                if attempt < 2:
                    self.retry_count += 1
                    time.sleep(0.05 * (attempt + 1))
        if result is None:
            raise A2AError(f"A2A transport failed after retries: {type(last_error).__name__}") from last_error
        if result.get("error"):
            raise A2AError(f"A2A {method} failed: {result['error'].get('message', 'unknown error')}")
        return result["result"]["task"]

    def send_and_wait(self, task_id: str, payload: dict[str, Any], message_id: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        message_id = message_id or str(uuid.uuid4())
        retries_before = self.retry_count
        sent = self._rpc("message/send", {"message": {"messageId": message_id, "role": "user", "parts": [{"kind": "data", "data": {"task_id": task_id, **payload}}]}})
        task_id_remote = sent["id"]
        trace = [{"method": "message/send", "task_id": task_id_remote, "state": sent["status"]["state"], "message_id": message_id}]
        deadline = time.monotonic() + self.timeout
        task = sent
        while task["status"]["state"] in {"submitted", "working", "input-required"}:
            if time.monotonic() >= deadline:
                task = self._rpc("tasks/cancel", {"id": task_id_remote})
                trace.append({"method": "tasks/cancel", "task_id": task_id_remote, "state": task["status"]["state"]})
                raise A2AError("A2A task deadline exceeded")
            time.sleep(self.poll_interval)
            task = self._rpc("tasks/get", {"id": task_id_remote})
            trace.append({"method": "tasks/get", "task_id": task_id_remote, "state": task["status"]["state"]})
        if task["status"]["state"] != "completed":
            raise A2AError(f"A2A task ended in {task['status']['state']}")
        artifact = task.get("artifacts", [{}])[0]
        data = next((part.get("data") for part in artifact.get("parts", []) if part.get("kind") == "data"), None)
        if not isinstance(data, dict):
            raise A2AError("A2A task completed without a DataPart artifact")
        if self.retry_count > retries_before:
            trace.append({"event": "transport_retries", "count": self.retry_count - retries_before})
        return data, trace

