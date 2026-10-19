"""Command line interface for OpsPilot investigations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .investigator import SOURCES, investigate


def load_incidents(path: Path) -> dict[str, dict[str, Any]]:
    incidents = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                incident = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
            incident_id = incident.get("incident_id")
            if not incident_id or incident_id in incidents:
                raise ValueError(f"missing or duplicate incident_id on line {line_number}")
            incidents[incident_id] = incident
    return incidents


def render_text(report: dict[str, Any]) -> str:
    lines = [f"Incident: {report['incident_id']}", f"Status: {report['status']}", f"Conclusion: {report['conclusion'] or 'insufficient evidence'}", "", "Hypothesis ledger:"]
    for hypothesis in report["hypotheses"]:
        lines.append(f"- {hypothesis['title']} (score={hypothesis['score']})")
        lines.append(f"  supports={','.join(hypothesis['support']) or 'none'}; against={','.join(hypothesis['against']) or 'none'}")
    lines.extend(["", "Queries:"])
    for query in report["query_trace"]:
        lines.append(f"- {query['source']} status={query['status']} params={json.dumps(query['params'], ensure_ascii=False)} records={query['record_count']}")
        for row in query["records"]:
            lines.append(f"  {row['record_id']}: {json.dumps(row, ensure_ascii=False, sort_keys=True)}")
    lines.append(f"\nCost: queries={report['cost']['query_count']} duration_ms={report['cost']['duration_ms']} tokens=not_applicable")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opspilot", description="本地只读故障调查原型")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("investigate", help="调查一个已知 incident")
    command.add_argument("incident_id")
    command.add_argument("--data", type=Path, default=Path("data/incidents.jsonl"))
    command.add_argument("--format", choices=("text", "json"), default="text")
    command.add_argument("--max-rows", type=int, default=100)
    command.add_argument("--missing-source", choices=SOURCES, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        incidents = load_incidents(args.data)
        if args.incident_id not in incidents:
            raise ValueError(f"incident not found: {args.incident_id}")
        report = investigate(incidents[args.incident_id], set(args.missing_source), args.max_rows)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else render_text(report)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

