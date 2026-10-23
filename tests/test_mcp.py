import json
import sys
import unittest
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from opspilot.mcp_servers import build_live_telemetry_server, build_logs_server, build_traces_server

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "incidents.jsonl"


class MCPInteroperabilityTests(unittest.TestCase):
    def test_live_mcp_exposes_only_read_tools(self):
        async def exercise():
            async with Client(build_live_telemetry_server()) as client:
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                self.assertEqual({"query_live_metrics", "query_live_logs", "query_live_traces",
                                  "query_live_deployments", "investigate_live_incident"}, names)
                self.assertFalse(any("inject" in name or "clear" in name for name in names))

        anyio.run(exercise)

    def test_standard_client_calls_read_only_logs_and_trace_servers(self):
        async def exercise():
            logs_server = build_logs_server(DATA)
            async with Client(logs_server) as client:
                tools = await client.list_tools()
                self.assertEqual(["query_logs"], [tool.name for tool in tools.tools])
                result = await client.call_tool("query_logs", {
                    "incident_id": "downstream-01",
                    "start": "2026-10-23T09:00:00Z",
                    "end": "2026-10-23T09:15:00Z",
                    "limit": 10,
                })
                payload = json.loads(result.content[0].text)
                self.assertEqual("downstream-01-l1", payload["records"][0]["record_id"])

            traces_server = build_traces_server(DATA)
            async with Client(traces_server) as client:
                tools = await client.list_tools()
                self.assertEqual(["query_traces"], [tool.name for tool in tools.tools])
                result = await client.call_tool("query_traces", {
                    "incident_id": "downstream-01",
                    "start": "2026-10-23T09:00:00Z",
                    "end": "2026-10-23T09:15:00Z",
                    "limit": 10,
                })
                payload = json.loads(result.content[0].text)
                self.assertEqual("tr-downstream-01-a91c", payload["records"][0]["attributes"]["trace_id"])

        anyio.run(exercise)

    def test_stdio_subprocess_interop_with_standard_client(self):
        async def exercise():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "opspilot.mcp_servers", "logs", "--data", str(DATA)],
                env={"PYTHONPATH": str(ROOT)},
            )
            async with Client(params) as client:
                result = await client.call_tool("query_logs", {
                    "incident_id": "pool-01",
                    "start": "2026-10-19T09:00:00Z",
                    "end": "2026-10-19T09:15:00Z",
                    "limit": 10,
                })
                payload = json.loads(result.content[0].text)
                self.assertEqual("pool-01-l1", payload["records"][0]["record_id"])

        anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
