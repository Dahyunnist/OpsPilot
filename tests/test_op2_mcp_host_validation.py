import json
import unittest
from types import SimpleNamespace

import anyio

from lab.verify_bad_release_mcp import EXPECTED_TOOLS, verify_client


class FakeMcpClient:
    def __init__(self, *, tool_names=None):
        self.tool_names = tool_names or EXPECTED_TOOLS
        self.calls = []
        self.log = {
            "record_id": "logs:bad-release",
            "trace_id": "trace-bad-release",
            "raw": {"order_id": "order-1", "trace_id": "trace-bad-release",
                    "dependency_host": "inventory-v2", "error_type": "URLError"},
        }
        self.deployment = {
            "record_id": "deployments:bad-release",
            "raw": {"run_id": "run-1", "order_id": "order-1", "version": "v2"},
        }
        self.trace = {
            "record_id": "traces:bad-release",
            "trace_id": "trace-bad-release",
            "service": "orders",
            "attributes": {"attributes": {"opspilot.order_id": "order-1"}},
        }

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.tool_names])

    async def call_tool(self, name, _arguments):
        self.calls.append(name)
        payloads = {
            "query_live_logs": {"records": [self.log]},
            "query_live_deployments": {"records": [self.deployment]},
            "query_live_traces": {"records": [self.trace]},
            "investigate_live_incident": {"status": "concluded", "evidence_refs": [
                {"source": "logs", "record_id": self.log["record_id"]},
                {"source": "deployments", "record_id": self.deployment["record_id"]},
                {"source": "traces", "record_id": self.trace["record_id"]},
            ]},
        }
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps(payloads[name]))])


class Op2McpHostValidationTests(unittest.TestCase):
    def test_read_only_mcp_calls_correlate_new_order_deployment_and_trace(self):
        client = FakeMcpClient()

        async def exercise():
            return await verify_client(
                client, start="2026-11-15T00:00:00Z", end="2026-11-15T00:01:00Z",
                order_id="order-1", run_id="run-1", wait_seconds=0,
            )

        report = anyio.run(exercise)
        self.assertEqual("passed", report["status"])
        self.assertEqual("completed", report["protocol"]["initialize"])
        self.assertEqual("trace-bad-release", report["evidence"]["trace_id"])
        self.assertEqual("v2", report["evidence"]["deployment_version"])
        self.assertIn("investigate_live_incident", client.calls)
        self.assertFalse(any("inject" in name or "delete" in name for name in client.calls))

    def test_unexpected_tool_set_is_rejected(self):
        client = FakeMcpClient(tool_names={"query_live_logs", "write_deployment"})

        async def exercise():
            await verify_client(
                client, start="2026-11-15T00:00:00Z", end="2026-11-15T00:01:00Z",
                order_id="order-1", run_id="run-1", wait_seconds=0,
            )

        with self.assertRaisesRegex(RuntimeError, "Unexpected read-only MCP tool set"):
            anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
