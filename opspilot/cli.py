"""Command line interface for OpsPilot investigations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .investigator import SOURCES, investigate
from .safety import audit_investigation, simulate_action
from .agents import investigate_multi_agent, investigate_single_agent
from .model_client import ModelError
from .a2a import A2AError
from .routing import select_route


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
    strategy = report["query_strategy"]
    comparison = report["comparison"]
    no_refutation = comparison["no_refutation"]
    lines = [f"Incident: {report['incident_id']}", f"Status: {report['status']}", f"Conclusion: {report['conclusion'] or 'insufficient evidence'}", f"Query strategy: {strategy['first']} then {', '.join(strategy['followups'])} ({strategy['reason']})", f"Logs-only: {comparison['logs_only']['conclusion'] or 'insufficient evidence'}", f"No-refutation baseline: {no_refutation['conclusion'] or 'insufficient evidence'}; supported competitors={','.join(no_refutation['supported_competitors']) or 'none'}", "", "Hypothesis ledger:"]
    for hypothesis in report["hypotheses"]:
        lines.append(f"- {hypothesis['title']} (score={hypothesis['score']})")
        lines.append(f"  supports={','.join(hypothesis['support']) or 'none'}; against={','.join(hypothesis['against']) or 'none'}")
    for rejected in report["rejected_hypotheses"]:
        lines.append(f"Rejected: {rejected['title']}; counter-evidence={','.join(rejected['counter_evidence'])}")
    lines.append(f"Cross-signal correlations: {json.dumps(report['correlations'], ensure_ascii=False)}")
    lines.append(f"Citation validation: {report['citation_validation']['all_references_exist']}")
    if report.get("recommendation"):
        lines.append(f"Approval card: {json.dumps(report['recommendation'], ensure_ascii=False)}")
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
    command.add_argument("--query-budget", type=int, default=8)
    command.add_argument("--missing-source", choices=SOURCES, action="append", default=[])
    command.add_argument("--inject", action="append", default=[], metavar="FAULT:SOURCE")
    command.add_argument("--state-file", type=Path)
    command.add_argument("--no-state", action="store_true")
    command.add_argument("--audit-file", type=Path, default=Path("var/audit.jsonl"))
    command.add_argument("--no-audit", action="store_true")
    command.add_argument("--mode", choices=("rules", "single-agent", "multi-agent", "auto"), default="rules")
    command.add_argument("--token-budget", type=int, default=12000)
    action_command = subparsers.add_parser("simulate-action", help="record an approval decision; approved actions remain simulation-only")
    action_command.add_argument("incident_id")
    action_command.add_argument("--approval", choices=("approve", "reject"), required=True)
    action_command.add_argument("--event-id", required=True)
    action_command.add_argument("--data", type=Path, default=Path("data/incidents.jsonl"))
    action_command.add_argument("--audit-file", type=Path, default=Path("var/audit.jsonl"))
    args = parser.parse_args(argv)
    try:
        incidents = load_incidents(args.data)
        if args.incident_id not in incidents:
            raise ValueError(f"incident not found: {args.incident_id}")
        if args.command == "simulate-action":
            report = investigate(incidents[args.incident_id])
            audit_investigation(report, args.audit_file)
            result = simulate_action(report, args.event_id, args.approval, args.audit_file)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        faults = {}
        for injected in args.inject:
            try:
                kind, source = injected.split(":", 1)
            except ValueError as exc:
                raise ValueError("--inject must use FAULT:SOURCE") from exc
            if kind not in {"timeout", "duplicate", "out_of_order"} or source not in SOURCES:
                raise ValueError(f"unsupported fault injection: {injected}")
            faults[source] = kind
        state_path = None if args.no_state else (args.state_file or Path("var/state") / f"{args.incident_id}.json")
        report = investigate(
            incidents[args.incident_id], set(args.missing_source), args.max_rows,
            query_budget=args.query_budget, fault_injections=faults, state_path=state_path,
        )
        if not args.no_audit:
            audit_investigation(report, args.audit_file)
        selected_mode = args.mode
        route_reason = None
        if args.mode == "auto":
            selected_mode, route_reason = select_route(report)
        if selected_mode == "single-agent":
            report = investigate_single_agent(incidents[args.incident_id], report, token_budget=min(args.token_budget, 1200))
        elif selected_mode == "multi-agent":
            if args.token_budget < 7200:
                raise ValueError("--token-budget must be at least 7200")
            report = investigate_multi_agent(incidents[args.incident_id], report, args.token_budget)
        if args.mode == "auto":
            report["routing"] = {"requested_mode": "auto", "selected_mode": selected_mode, "reason": route_reason}
    except (OSError, ValueError, ModelError, A2AError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" or args.mode in {"single-agent", "multi-agent", "auto"} else render_text(report)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
