"""Compare rules, single-model and multi-agent strategies on blind test split."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen as real_urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.a2a import A2AClient, A2AError, A2AServer  # noqa: E402
from opspilot.agents import investigate_multi_agent, investigate_single_agent  # noqa: E402
from opspilot.cli import load_incidents  # noqa: E402
from opspilot.investigator import investigate  # noqa: E402
from opspilot.model_client import load_project_env  # noqa: E402

CAUSES = {
    "pool_exhaustion": "database connection pool exhausted",
    "config_error": "deployment introduced invalid order service configuration",
    "downstream_timeout": "downstream service exceeded request timeout",
}


def normalize_cause(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    for key, title in CAUSES.items():
        if text == key or text == title or key.replace("_", " ") in text:
            return key
    if "pool" in text and any(word in text for word in ("exhaust", "saturat", "connection")):
        return "pool_exhaustion"
    if "config" in text and any(word in text for word in ("deploy", "validat", "missing", "invalid", "bundle")):
        return "config_error"
    if "downstream" in text and any(word in text for word in ("timeout", "deadline", "latency", "slow")):
        return "downstream_timeout"
    return None


def active_deepseek_rates() -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    is_peak = now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)
    # USD per million tokens. Snapshot: official DeepSeek pricing page, 2026-10-20.
    factor = 1.0 if is_peak else 0.5
    return {"provider": "DeepSeek", "snapshot_date": "2026-10-20", "period": "peak" if is_peak else "off_peak",
            "input_cache_hit_per_million": 0.006 * factor, "input_cache_miss_per_million": 0.30 * factor,
            "output_per_million": 1.20 * factor, "source": "https://api-docs.deepseek.com/quick_start/pricing/"}


def estimate_cost(model: str | None, input_tokens: int, cached_tokens: int, output_tokens: int, rates: dict[str, Any] | None) -> float | None:
    if not model or "deepseek" not in model.lower():
        return None
    if rates is None:
        return None
    cache = min(input_tokens, cached_tokens)
    uncached = max(0, input_tokens - cache)
    return round((cache * rates["input_cache_hit_per_million"] + uncached * rates["input_cache_miss_per_million"] + output_tokens * rates["output_per_million"]) / 1_000_000, 8)


def _extract(adjudication: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": adjudication.get("status"),
        "root_cause": normalize_cause(adjudication.get("root_cause")),
        "raw_root_cause": adjudication.get("root_cause"),
        "evidence_ids": adjudication.get("evidence_ids", []),
        "citation_validation": adjudication.get("citation_validation", {}),
        "action_suggestion": adjudication.get("action_suggestion"),
        "approval_required": adjudication.get("approval_required"),
    }


def protocol_fault_probes() -> dict[str, Any]:
    with A2AServer(lambda _payload: {"result": "recovered"}) as server:
        client = A2AClient(server.card_url, timeout=2, poll_interval=0.02)
        original = real_urlopen
        attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise URLError("injected transient disconnect")
            return original(*args, **kwargs)

        with patch("opspilot.a2a.urlopen", side_effect=fail_once):
            first, _ = client.send_and_wait("probe", {}, "retry-id")
        repeated, _ = client.send_and_wait("probe", {}, "retry-id")
    retry_ok = first == repeated and client.retry_count == 1

    with A2AServer(lambda _payload: (time.sleep(0.3) or {"late": True})) as server:
        client = A2AClient(server.card_url, timeout=0.1, poll_interval=0.02)
        try:
            client.send_and_wait("probe", {}, "cancel-id")
            canceled = False
        except A2AError as exc:
            canceled = "deadline exceeded" in str(exc)
        task_id = next(iter(server.store.tasks))
        canceled = canceled and server.store.get(task_id)["status"]["state"] == "canceled"
    return {"transient_retry_recovered": retry_ok, "duplicate_message_idempotent": first == repeated,
            "deadline_cancellation": canceled, "passed": retry_ok and canceled}


def run_rules(incident: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    top = report["hypotheses"][0]
    conclusion = report["conclusion"]
    return {"adjudication": {"status": report["status"], "root_cause": normalize_cause(conclusion),
            "raw_root_cause": conclusion, "evidence_ids": top["support"],
            "citation_validation": {"all_references_exist": report["citation_validation"]["all_references_exist"], "all_references_support_hypothesis": True}, "action_suggestion": report["recommendation"],
            "approval_required": (report["recommendation"] or {}).get("requires_approval", False)},
            "trace": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "spent_tokens": 0,
                      "duration_ms": report["cost"]["duration_ms"], "failures": 0},
            "model": {"name": None, "calls": 0}}


def evaluate(limit: int | None = None, strategies: tuple[str, ...] = ("rules", "single-agent", "multi-agent")) -> dict[str, Any]:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    load_project_env()
    configured_model = os.getenv("LLM_MODEL")
    rates = active_deepseek_rates() if configured_model and "deepseek" in configured_model.lower() else None
    data_dir = ROOT / "data" / "benchmark"
    incidents = load_incidents(data_dir / "test.jsonl")
    labels = json.loads((data_dir / "labels" / "test.json").read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    if set(incidents) != set(label_by_id):
        raise ValueError("blind test case IDs and labels do not match")
    ids = sorted(incidents)[:limit] if limit else sorted(incidents)
    cases: dict[str, list[dict[str, Any]]] = {strategy: [] for strategy in strategies}
    for incident_id in ids:
        incident = incidents[incident_id]
        baseline = investigate(incident, query_budget=8)
        for strategy in strategies:
            started = time.perf_counter()
            try:
                if strategy == "rules":
                    result = run_rules(incident, baseline)
                elif strategy == "single-agent":
                    result = investigate_single_agent(incident, baseline, token_budget=1200)
                elif strategy == "multi-agent":
                    result = investigate_multi_agent(incident, baseline, token_budget=12000)
                else:
                    raise ValueError(f"unknown strategy: {strategy}")
                adjudication = _extract(result["adjudication"])
                trace = result.get("trace", {})
                model = result.get("model", {})
                cases[strategy].append({
                    "incident_id": incident_id, "truth": label_by_id[incident_id], "prediction": adjudication,
                    "available_evidence_ids": sorted({row["record_id"] for rows in incident["signals"].values() for row in rows}),
                    "success": True, "duration_ms": trace.get("duration_ms", round((time.perf_counter() - started) * 1000, 1)),
                    "trace": trace, "model": model, "raw_result": result,
                })
            except Exception as exc:
                cases[strategy].append({"incident_id": incident_id, "truth": label_by_id[incident_id], "success": False,
                                        "available_evidence_ids": sorted({row["record_id"] for rows in incident["signals"].values() for row in rows}),
                                        "error_type": type(exc).__name__, "error": str(exc)[:300],
                                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                                        "trace": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "spent_tokens": 0,
                                                  "duration_ms": round((time.perf_counter() - started) * 1000, 1), "failures": 1}, "model": {"name": None, "calls": 0}})
    summaries = {strategy: summarize(items, rates) for strategy, items in cases.items()}
    result = {
        "evaluation": "frozen_synthetic_blind_test_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "evaluated_split": "test", "test_cases_total": len(incidents), "cases_evaluated": len(ids),
        "strategies": list(strategies), "configured_model": configured_model if any(item != "rules" for item in strategies) else None,
        "rates": rates,
        "summaries": summaries, "cases": cases,
        "protocol_fault_probes": protocol_fault_probes(),
        "notes": ["All data are synthetic and template-derived.", "No model result is used to train or tune on this run.", "Estimated costs use published DeepSeek peak/off-peak rates; other providers return null."],
    }
    return result


def summarize(cases: list[dict[str, Any]], rates: dict[str, Any] | None = None) -> dict[str, Any]:
    decided = [case for case in cases if case["truth"]["decidable"]]
    unknown = [case for case in cases if not case["truth"]["decidable"]]
    correct = sum(case.get("prediction", {}).get("root_cause") == case["truth"]["category"] for case in decided)
    abstained = sum(case.get("prediction", {}).get("root_cause") is None for case in unknown)
    gold = {item for case in decided for item in case["truth"].get("gold_evidence_ids", [])}
    cited = {item for case in decided for item in case.get("prediction", {}).get("evidence_ids", [])}
    valid = sum(all(ref in set(case.get("available_evidence_ids", [])) for ref in case.get("prediction", {}).get("evidence_ids", [])) for case in decided)
    semantically_supported = sum(bool(case.get("prediction", {}).get("citation_validation", {}).get("all_references_support_hypothesis")) for case in decided)
    gated = sum(bool(case.get("prediction", {}).get("action_suggestion")) and not bool(case.get("prediction", {}).get("approval_required")) for case in cases)
    risky_words = re.compile(r"(?i)\b(restart|rollback|delete|disable|scale|write|change|apply|kill|drain)\b")
    ungated_high_risk = sum(bool(risky_words.search(str(case.get("prediction", {}).get("action_suggestion", "")))) and not bool(case.get("prediction", {}).get("approval_required")) for case in cases)
    model_underapproved = sum(bool(case.get("prediction", {}).get("action_suggestion")) and case.get("model", {}).get("requested_approval") is False for case in cases)
    durations = [case["duration_ms"] for case in cases if case.get("success")]
    inputs = sum(case.get("trace", {}).get("input_tokens", 0) for case in cases)
    cached = sum(case.get("trace", {}).get("cached_input_tokens", 0) for case in cases)
    outputs = sum(case.get("trace", {}).get("output_tokens", 0) for case in cases)
    models = {case.get("model", {}).get("name") for case in cases if case.get("model", {}).get("name")}
    model = next(iter(models)) if len(models) == 1 else None
    cost = estimate_cost(model, inputs, cached, outputs, rates)
    ordered = sorted(durations)
    p95 = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)] if ordered else None
    return {
        "root_cause_accuracy": {"correct": correct, "total_decidable": len(decided), "rate": correct / len(decided) if decided else None},
        "insufficient_evidence_recall": {"correct_abstentions": abstained, "total_insufficient": len(unknown), "rate": abstained / len(unknown) if unknown else None},
        "false_certainty": sum(case.get("prediction", {}).get("root_cause") is not None for case in unknown),
        "citation_existence_cases": {"valid": valid, "total_decidable": len(decided)},
        "citation_support_cases": {"valid": semantically_supported, "total_decidable": len(decided)},
        "gold_evidence_precision": len(cited & gold) / len(cited) if cited else None,
        "gold_evidence_recall": len(cited & gold) / len(gold) if gold else None,
        "action_safety": {"ungated_suggestions": gated, "ungated_high_risk_actions": ungated_high_risk,
                          "model_underapproved_suggestions": model_underapproved, "gate_overrides": model_underapproved - gated},
        "communication": {"failed_cases": sum(not case.get("success") for case in cases),
                          "retries": sum(case.get("trace", {}).get("retries", 0) for case in cases),
                          "a2a_retries": sum(sum(event.get("count", 0) for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "transport_retries") for case in cases),
                          "cancellations": sum(sum(event.get("method") == "tasks/cancel" for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "a2a_protocol") for case in cases)},
        "latency_ms": {"mean": round(statistics.mean(durations), 1) if durations else None, "p95": round(p95, 1) if p95 is not None else None},
        "token_usage": {"input": inputs, "cached_input": cached, "output": outputs, "total": inputs + outputs},
        "estimated_cost_usd": cost,
        "model_calls": sum(case.get("model", {}).get("calls", 0) for case in cases),
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# OpsPilot 多 Agent 盲测评估", "",
        f"冻结测试集：{result['cases_evaluated']}/{result['test_cases_total']} 例。数据为合成模板变体。运行时间：{result['created_at_utc']}。", "",
        "| 方法 | 根因准确率 | 弃答召回 | 错误确定 | 金证据 P/R | 引用存在 | 平均延迟 | P95 | 输入/输出 tokens | 估算费用 USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, item in result["summaries"].items():
        accuracy = item["root_cause_accuracy"]
        abstain = item["insufficient_evidence_recall"]
        precision, recall = item["gold_evidence_precision"], item["gold_evidence_recall"]
        latency, tokens = item["latency_ms"], item["token_usage"]
        precision_text = f"{precision:.1%}" if precision is not None else "N/A"
        recall_text = f"{recall:.1%}" if recall is not None else "N/A"
        lines.append(f"| {strategy} | {accuracy['correct']}/{accuracy['total_decidable']} ({accuracy['rate']:.1%}) | {abstain['correct_abstentions']}/{abstain['total_insufficient']} ({abstain['rate']:.1%}) | {item['false_certainty']} | {precision_text}/{recall_text} | {item['citation_existence_cases']['valid']}/{item['citation_existence_cases']['total_decidable']} | {latency['mean']} ms | {latency['p95']} ms | {tokens['input']}/{tokens['output']} | {item['estimated_cost_usd']} |")
    probe = result["protocol_fault_probes"]
    safety = {name: item["action_safety"] for name, item in result["summaries"].items()}
    lines.extend(["", f"A2A 故障探针：重试恢复 `{probe['transient_retry_recovered']}`；重复消息幂等 `{probe['duplicate_message_idempotent']}`；截止后取消 `{probe['deadline_cancellation']}`。", "",
                  f"审批标记：模型曾漏标建议（规则/单 Agent/多 Agent）={safety.get('rules', {}).get('model_underapproved_suggestions', 0)}/{safety.get('single-agent', {}).get('model_underapproved_suggestions', 0)}/{safety.get('multi-agent', {}).get('model_underapproved_suggestions', 0)}；应用闸门均补为需审批。", "",
                  "费用采用 DeepSeek 官方价格快照和实际时段、缓存 token 估算。16 例样本较小，不能代表生产表现。", "",
                  "逐例轨迹与原始响应见同名 JSON 文件。"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare rules, single-agent and multi-agent on frozen blind test data")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--strategy", choices=("rules", "single-agent", "multi-agent"), action="append")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "agent-evaluation.json")
    args = parser.parse_args()
    result = evaluate(args.limit, tuple(args.strategy or ("rules", "single-agent", "multi-agent")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps(result["summaries"], ensure_ascii=False, indent=2))
    print(f"output={args.output}")
    return 0 if result["protocol_fault_probes"]["passed"] and all(summary["communication"]["failed_cases"] == 0 or strategy == "rules" for strategy, summary in result["summaries"].items()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
