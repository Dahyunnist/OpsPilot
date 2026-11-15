"""Run read-only MCP correlation against one isolated bad_release window."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from mcp import Client, StdioServerParameters


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "query_live_metrics",
    "query_live_logs",
    "query_live_traces",
    "query_live_deployments",
    "investigate_live_incident",
}


def _payload(result: Any, tool_name: str) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool returned an error: {tool_name}")
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            value = json.loads(text)
            if isinstance(value, dict):
                return value
    raise RuntimeError(f"MCP tool returned no JSON object: {tool_name}")


def _same_order_trace(row: dict[str, Any], order_id: str, trace_id: str) -> bool:
    attributes = row.get("attributes", {})
    tags = attributes.get("attributes", {}) if isinstance(attributes, dict) else {}
    return (
        row.get("trace_id") == trace_id
        and row.get("service") == "orders"
        and isinstance(tags, dict)
        and tags.get("opspilot.order_id") == order_id
    )


async def verify_client(client: Any, *, start: str, end: str, order_id: str,
                        run_id: str, wait_seconds: float = 12.0) -> dict[str, Any]:
    listed = await client.list_tools()
    names = {tool.name for tool in listed.tools}
    if names != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected read-only MCP tool set: {sorted(names)}")

    arguments = {"start": start, "end": end, "limit": 1000}
    calls = {"query_live_logs": 0, "query_live_deployments": 0, "query_live_traces": 0}
    deadline = time.monotonic() + wait_seconds
    selected: tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None = None
    while True:
        logs = _payload(await client.call_tool("query_live_logs", arguments), "query_live_logs")
        deployments = _payload(await client.call_tool("query_live_deployments", arguments), "query_live_deployments")
        traces = _payload(await client.call_tool("query_live_traces", arguments), "query_live_traces")
        calls["query_live_logs"] += 1
        calls["query_live_deployments"] += 1
        calls["query_live_traces"] += 1

        log_rows = [row for row in logs.get("records", [])
                    if row.get("raw", {}).get("order_id") == order_id]
        deployment_rows = [row for row in deployments.get("records", [])
                           if row.get("raw", {}).get("run_id") == run_id
                           and row.get("raw", {}).get("order_id") == order_id
                           and row.get("raw", {}).get("version") == "v2"]
        for log in log_rows:
            raw = log.get("raw", {})
            trace_id = raw.get("trace_id")
            dns = raw.get("dependency_host") == "inventory-v2" and raw.get("error_type") == "URLError"
            linked = [row for row in traces.get("records", [])
                      if isinstance(trace_id, str) and _same_order_trace(row, order_id, trace_id)]
            if dns and len(deployment_rows) == 1 and linked:
                selected = (log, deployment_rows[0], linked)
                break
        if selected:
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)

    if not selected:
        raise RuntimeError(f"MCP evidence did not correlate log, deployment and trace for {order_id}")

    log, deployment, trace_rows = selected
    investigation = _payload(
        await client.call_tool("investigate_live_incident", arguments), "investigate_live_incident"
    )
    calls["investigate_live_incident"] = 1
    required_refs = {
        ("logs", log.get("record_id")),
        ("deployments", deployment.get("record_id")),
        *(('traces', row.get("record_id")) for row in trace_rows),
    }
    result_refs = {
        (row.get("source"), row.get("record_id"))
        for row in investigation.get("evidence_refs", [])
    }
    if not required_refs.issubset(result_refs):
        raise RuntimeError("MCP investigation omitted correlated log, deployment or trace evidence")

    return {
        "status": "passed",
        "protocol": {"initialize": "completed", "tools_list": "passed", "read_only_tool_calls": "passed"},
        "tools": sorted(names),
        "read_calls": calls,
        "order_id": order_id,
        "run_id": run_id,
        "evidence": {
            "log_record_id": log.get("record_id"),
            "deployment_record_id": deployment.get("record_id"),
            "deployment_version": deployment.get("raw", {}).get("version"),
            "trace_id": log.get("trace_id"),
            "trace_record_ids": sorted(row.get("record_id") for row in trace_rows),
            "dns_dependency_host": log.get("raw", {}).get("dependency_host"),
            "investigation_status": investigation.get("status"),
            "investigation_evidence_count": len(result_refs),
        },
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opspilot.mcp_servers", "live"],
        env=environment,
    )
    async with Client(params) as client:
        return await verify_client(
            client, start=args.start, end=args.end, order_id=args.order_id, run_id=args.run_id
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))
    except Exception as exc:
        print(f"MCP host verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
