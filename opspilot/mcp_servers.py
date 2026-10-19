"""Read-only MCP servers for local log and trace data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .cli import load_incidents
from .investigator import query_source


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve one local read-only OpsPilot MCP source over stdio")
    parser.add_argument("source", choices=("logs", "traces"))
    parser.add_argument("--data", type=Path, default=Path("data/incidents.jsonl"))
    args = parser.parse_args(argv)
    server = build_logs_server(args.data) if args.source == "logs" else build_traces_server(args.data)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
