from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _decorated_tools(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        )
    }


def test_a2a_source_declares_local_v03_card_and_jsonrpc_task_methods():
    source = _source("opspilot/a2a.py")
    for contract in (
        '"protocolVersion": "0.3.0"',
        '"preferredTransport": "JSONRPC"',
        '"/.well-known/agent-card.json"',
        'method == "message/send"',
        'method == "tasks/get"',
        'method == "tasks/cancel"',
        '("127.0.0.1", 0)',
        '"result": store.get(task["id"])',
    ):
        assert contract in source


def test_mcp_source_registers_only_documented_read_query_tools():
    source = _source("opspilot/mcp_servers.py")
    assert _decorated_tools(source) == {
        "query_logs",
        "query_traces",
        "query_live_metrics",
        "query_live_logs",
        "query_live_traces",
        "query_live_deployments",
        "investigate_live_incident",
    }
    assert "def execute" not in source
    assert "def inject" not in source
    assert "def clear" not in source
