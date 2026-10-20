"""Cost-aware routing based only on live investigation evidence."""

from __future__ import annotations

from typing import Any


def select_route(report: dict[str, Any]) -> tuple[str, str]:
    """Use one model only when live evidence supports competing cross-source causes."""
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
    if report.get("status") == "determined" and len(independent_candidates) > 1:
        return "single-agent", "多个根因各有跨源强证据；单 Agent 复核以提升引用精度"
    return "rules", "没有可判定的跨源强证据冲突；保留规则结论或弃答，避免无收益模型调用"
