"""Cost-aware routing based only on live investigation evidence."""

from __future__ import annotations

from typing import Any


def select_route(report: dict[str, Any]) -> tuple[str, str]:
    """Escalate only when live cross-source evidence needs model adjudication."""
    cost = report.get("cost")
    if report.get("query_budget_exhausted") or (isinstance(cost, dict) and cost.get("budget_exhausted")):
        return "rules", "遥测查询预算已耗尽；保留当前规则结论或弃答，不升级模型"
    source_by_id = {
        row["record_id"]: query["source"]
        for query in report.get("query_trace", [])
        for row in query.get("records", [])
    }
    independent_candidates = []
    for hypothesis in report.get("hypotheses", []):
        support = set(hypothesis.get("strong_support", []))
        sources = {source_by_id[item] for item in support if item in source_by_id}
        if len(support) >= 2 and len(sources) >= 2:
            independent_candidates.append(hypothesis.get("hypothesis_id"))
    if len(independent_candidates) > 1:
        return "multi-agent", "多个根因各有跨源强证据；多 Agent 复核冲突并提取完整根因集"
    if report.get("status") == "insufficient_evidence" and len(independent_candidates) == 1:
        return "single-agent", "存在一个跨源强候选但规则仍弃答；单 Agent 复核因果方向"
    return "rules", "没有需要模型裁决的跨源强证据冲突；保留规则结论"
