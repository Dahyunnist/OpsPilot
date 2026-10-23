"""Read-only MCP servers for local log and trace data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .cli import load_incidents
from .investigator import query_source
from .live_investigation import investigate_live
from .telemetry import LabTelemetry


def build_logs_server(data_path: Path = Path("data/incidents.jsonl")) -> MCPServer:
    incidents = load_incidents(data_path)
    server = MCPServer("OpsPilot logs", instructions="Read-only log queries over local synthetic incident data.")

    @server.tool()
    def query_logs(incident_id: str, start: str, end: str, limit: int = 100) -> str:
        """Return original log records in a bounded UTC time window."""
        if incident_id not in incidents:
            raise ValueError(f"incident not found: {incident_id}")
        incident = incidents[incident_id]
        selected = dict(incident)
        selected["window"] = {"start": start, "end": end}
        return json.dumps(query_source(selected, "logs", max_rows=limit), ensure_ascii=False)

    return server


def build_traces_server(data_path: Path = Path("data/incidents.jsonl")) -> MCPServer:
    incidents = load_incidents(data_path)
    server = MCPServer("OpsPilot traces", instructions="Read-only trace queries over local synthetic incident data.")

    @server.tool()
    def query_traces(incident_id: str, start: str, end: str, limit: int = 100) -> str:
        """Return original trace span records in a bounded UTC time window."""
        if incident_id not in incidents:
            raise ValueError(f"incident not found: {incident_id}")
        incident = incidents[incident_id]
        selected = dict(incident)
        selected["window"] = {"start": start, "end": end}
        return json.dumps(query_source(selected, "traces", max_rows=limit), ensure_ascii=False)

    return server


def build_live_telemetry_server() -> MCPServer:
    adapter = LabTelemetry()
    server = MCPServer("OpsPilot live lab telemetry", instructions=(
        "Read-only access to local Prometheus, Jaeger, structured service logs and deployment records. "
        "All queries require a bounded UTC time window and a row limit. No injection or write tools exist."
    ))

    @server.tool()
    def query_live_metrics(start: str, end: str, limit: int = 100) -> str:
        """Query bounded Prometheus samples from the local fault lab."""
        return json.dumps(adapter.metrics(start, end, limit), ensure_ascii=False)

    @server.tool()
    def query_live_logs(start: str, end: str, limit: int = 100) -> str:
        """Query bounded structured logs from local lab services."""
        return json.dumps(adapter.logs(start, end, limit), ensure_ascii=False)

    @server.tool()
    def query_live_traces(start: str, end: str, limit: int = 100) -> str:
        """Query bounded Jaeger spans from the local fault lab."""
        return json.dumps(adapter.traces(start, end, limit), ensure_ascii=False)

    @server.tool()
    def query_live_deployments(start: str, end: str, limit: int = 100) -> str:
        """Query bounded deployment events from the local fault lab."""
        return json.dumps(adapter.deployments(start, end, limit), ensure_ascii=False)

    @server.tool()
    def investigate_live_incident(start: str, end: str, limit: int = 100) -> str:
        """Correlate live read-only evidence; abstain if independent support is insufficient."""
        return json.dumps(investigate_live(start, end, limit, adapter), ensure_ascii=False)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a local read-only OpsPilot MCP source over stdio")
    parser.add_argument("source", choices=("logs", "traces", "live"))
    parser.add_argument("--data", type=Path, default=Path("data/incidents.jsonl"))
    args = parser.parse_args(argv)
    server = build_live_telemetry_server() if args.source == "live" else build_logs_server(args.data) if args.source == "logs" else build_traces_server(args.data)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
