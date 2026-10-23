"""Compare rules, single-model and multi-agent strategies on blind test split."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import threading
import uuid
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
from opspilot.model_client import ModelError, load_project_env  # noqa: E402
from opspilot.routing import select_route  # noqa: E402

CAUSES = {
    "pool_exhaustion": "database connection pool exhausted",
    "config_error": "deployment introduced invalid order service configuration",
    "downstream_timeout": "downstream service exceeded request timeout",
}


class BudgetLimitExceeded(RuntimeError):
    pass


class RunBudget:
    """Track request charges and stop before a request can exceed the run cap."""

    def __init__(self, limit_usd: float, ledger_path: Path, model: str) -> None:
        self.limit_usd = limit_usd
        self.ledger_path = ledger_path
        self.model = model
        self.spent_usd = 0.0
        self.prior_spent_usd = self._prior_spend()
        self.reserved_usd = 0.0
        self.lock = threading.Lock()
        self.run_id = str(uuid.uuid4())
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def _prior_spend(self) -> float:
        if not self.ledger_path.exists():
            return 0.0
        try:
            records = [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            raise ValueError("cost ledger is unreadable; refusing paid requests")
        summaries = [row for row in records if row.get("kind") == "run_summary"]
        summarized_ids = {row.get("run_id") for row in summaries if row.get("run_id")}
        total = sum(float(row.get("peak_rate_estimate_usd", 0)) for row in summaries)
        incomplete: dict[str, float] = {}
        for row in records:
            run_id = row.get("run_id")
            if not run_id or run_id in summarized_ids:
                continue
            if row.get("kind") in {"completed_request", "failed_request_worst_case"}:
                incomplete[run_id] = max(incomplete.get(run_id, 0.0), float(row.get("run_peak_estimate_usd", 0)))
        return total + sum(incomplete.values())

    def _append(self, record: dict[str, Any]) -> None:
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def call(self, operation: Any, instance: Any, system: str, user: str, token_budget: int) -> dict[str, Any]:
        max_output = min(instance.max_tokens, max(64, token_budget))
        input_reserve = int((len(system.encode("utf-8")) + len(user.encode("utf-8"))) * 1.2) + 256
        reserve = (input_reserve * 0.30 + max_output * 1.20) / 1_000_000
        with self.lock:
            if self.prior_spent_usd + self.spent_usd + self.reserved_usd + reserve > self.limit_usd:
                raise BudgetLimitExceeded(f"累计峰值费率预留将超过账本上限 ${self.limit_usd:.2f}")
            self.reserved_usd += reserve
        try:
            result = operation(instance, system, user, token_budget)
        except Exception as exc:
            with self.lock:
                self.reserved_usd -= reserve
                self.spent_usd += reserve
                self._append({"kind": "failed_request_worst_case", "run_id": self.run_id, "model": self.model, "reserve_usd": round(reserve, 8),
                              "run_peak_estimate_usd": round(self.spent_usd, 8), "cumulative_peak_estimate_usd": round(self.prior_spent_usd + self.spent_usd, 8), "limit_usd": self.limit_usd,
                              "error_type": type(exc).__name__,
                              "error_detail": str(exc)[:180] if isinstance(exc, ModelError) else None})
            raise
        usage = result.get("usage", {})
        actual = estimate_cost(self.model, usage.get("prompt_tokens", 0),
            usage.get("prompt_tokens_details", {}).get("cached_tokens", 0), usage.get("completion_tokens", 0),
            {"input_cache_hit_per_million": 0.006, "input_cache_miss_per_million": 0.30, "output_per_million": 1.20}) or 0.0
        with self.lock:
            self.reserved_usd -= reserve
            self.spent_usd += actual
            self._append({"kind": "completed_request", "run_id": self.run_id, "model": self.model,
                "input_tokens": usage.get("prompt_tokens", 0), "cached_input_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0), "reserve_usd": round(reserve, 8),
                "peak_rate_estimate_usd": round(actual, 8), "run_peak_estimate_usd": round(self.spent_usd, 8),
                "cumulative_peak_estimate_usd": round(self.prior_spent_usd + self.spent_usd, 8),
                "limit_usd": self.limit_usd})
        return result


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
    root_cause = normalize_cause(adjudication.get("root_cause"))
    ranked = []
    ranked_evidence = []
    for item in adjudication.get("ranked_causes", []) if isinstance(adjudication.get("ranked_causes", []), list) else []:
        value = normalize_cause(item.get("id", item.get("cause")) if isinstance(item, dict) else item)
        if value and value not in ranked:
            ranked.append(value)
        if isinstance(item, dict) and isinstance(item.get("evidence_ids"), list):
            ranked_evidence.extend(ref for ref in item["evidence_ids"] if isinstance(ref, str) and ref not in ranked_evidence)
    if root_cause and root_cause not in ranked:
        ranked.insert(0, root_cause)
    return {
        "status": adjudication.get("status"),
        "root_cause": root_cause,
        "ranked_causes": ranked[:3],
        "raw_root_cause": adjudication.get("root_cause"),
        "evidence_ids": ranked_evidence or adjudication.get("evidence_ids", []),
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


def evaluate(limit: int | None = None, strategies: tuple[str, ...] = ("rules", "single-agent", "multi-agent"),
             benchmark_dir: Path | None = None, split: str = "test", max_cost_usd: float | None = None,
             cost_ledger: Path | None = None) -> dict[str, Any]:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    if split not in {"validation", "test"}:
        raise ValueError("--split must be validation or test")
    load_project_env()
    configured_model = os.getenv("LLM_MODEL")
    if max_cost_usd is not None and (max_cost_usd <= 0 or not configured_model or "deepseek" not in configured_model.lower()):
        raise ValueError("a positive --max-cost-usd requires a configured DeepSeek model")
    run_budget = RunBudget(max_cost_usd, cost_ledger or ROOT / "reports" / "api-cost-ledger.jsonl", configured_model) if max_cost_usd is not None else None
    rates = active_deepseek_rates() if configured_model and "deepseek" in configured_model.lower() else None
    data_dir = benchmark_dir or ROOT / "data" / "benchmark"
    incidents = load_incidents(data_dir / f"{split}.jsonl")
    labels = json.loads((data_dir / "labels" / f"{split}.json").read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    data_bytes = (data_dir / f"{split}.jsonl").read_bytes()
    label_bytes = (data_dir / "labels" / f"{split}.json").read_bytes()
    split_manifest = manifest["splits"][split]
    if hashlib.sha256(data_bytes).hexdigest() != split_manifest["data_sha256"] or hashlib.sha256(label_bytes).hexdigest() != split_manifest["labels_sha256"]:
        raise ValueError("frozen test split hash mismatch")
    if set(incidents) != set(label_by_id):
        raise ValueError("blind test case IDs and labels do not match")
    ids = sorted(incidents)[:limit] if limit else sorted(incidents)
    cases: dict[str, list[dict[str, Any]]] = {strategy: [] for strategy in strategies}
    budget_stop_reason = None
    for incident_id in ids:
        incident = incidents[incident_id]
        baseline = investigate(incident, query_budget=8)
        for strategy in strategies:
            selected_strategy, route_reason = select_route(baseline) if strategy == "auto" else (strategy, None)
            started = time.perf_counter()
            measured = {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "failures": 0}
            original_complete = __import__("opspilot.model_client", fromlist=["ChatModel"]).ChatModel.complete

            def tracked_complete(instance: Any, system: str, user: str, token_budget: int) -> dict[str, Any]:
                measured["calls"] += 1
                try:
                    result = run_budget.call(original_complete, instance, system, user, token_budget) if run_budget else original_complete(instance, system, user, token_budget)
                except Exception:
                    measured["failures"] += 1
                    raise
                usage = result.get("usage", {})
                measured["input_tokens"] += usage.get("prompt_tokens", 0)
                measured["cached_input_tokens"] += usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                measured["output_tokens"] += usage.get("completion_tokens", 0)
                return result
            try:
                with patch("opspilot.model_client.ChatModel.complete", tracked_complete):
                    if strategy == "rules":
                        result = run_rules(incident, baseline)
                    elif selected_strategy == "rules":
                        result = run_rules(incident, baseline)
                    elif selected_strategy == "single-agent":
                        result = investigate_single_agent(incident, baseline, token_budget=1200)
                    elif selected_strategy == "multi-agent":
                        result = investigate_multi_agent(incident, baseline, token_budget=12000)
                    else:
                        raise ValueError(f"unknown strategy: {strategy}")
                if strategy == "auto":
                    result["routing"] = {"selected_mode": selected_strategy, "reason": route_reason}
                adjudication = _extract(result["adjudication"])
                trace = result.get("trace", {})
                model = result.get("model", {})
                cases[strategy].append({
                    "incident_id": incident_id, "truth": label_by_id[incident_id], "prediction": adjudication,
                    "available_evidence_ids": sorted({row["record_id"] for rows in incident["signals"].values() for row in rows}),
                    "success": True, "duration_ms": trace.get("duration_ms", round((time.perf_counter() - started) * 1000, 1)),
                    "trace": trace, "model": model, "route": result.get("routing"), "raw_result": result,
                })
            except Exception as exc:
                cases[strategy].append({"incident_id": incident_id, "truth": label_by_id[incident_id], "success": False,
                                        "available_evidence_ids": sorted({row["record_id"] for rows in incident["signals"].values() for row in rows}),
                                        "error_type": type(exc).__name__, "error": str(exc)[:300],
                                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                                        "trace": {"input_tokens": measured["input_tokens"], "cached_input_tokens": measured["cached_input_tokens"],
                                                  "output_tokens": measured["output_tokens"], "spent_tokens": measured["input_tokens"] + measured["output_tokens"],
                                                  "duration_ms": round((time.perf_counter() - started) * 1000, 1), "failures": max(1, measured["failures"])},
                                        "model": {"name": configured_model, "calls": measured["calls"]}})
                if isinstance(exc, BudgetLimitExceeded):
                    budget_stop_reason = str(exc)
                    break
        if budget_stop_reason:
            break
    summaries = {strategy: summarize(items, rates) for strategy, items in cases.items()}
    auto_routes: dict[str, int] = {}
    for item in cases.get("auto", []):
        route = item.get("route", {}).get("selected_mode", "failed")
        auto_routes[route] = auto_routes.get(route, 0) + 1
    result = {
        "evaluation": manifest.get("benchmark_id", "frozen_synthetic_blind_test_v1"),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "evaluated_split": split, "test_cases_total": len(incidents), "cases_evaluated": len(ids),
        "strategies": list(strategies), "configured_model": configured_model if any(item != "rules" for item in strategies) else None,
        "rates": rates,
        "summaries": summaries, "cases": cases,
        "auto_route_distribution": auto_routes,
        "run_budget": {"limit_usd": max_cost_usd,
                       "peak_rate_estimate_usd": round(run_budget.spent_usd, 8) if run_budget else None,
                       "prior_peak_estimate_usd": round(run_budget.prior_spent_usd, 8) if run_budget else None,
                       "cumulative_peak_estimate_usd": round(run_budget.prior_spent_usd + run_budget.spent_usd, 8) if run_budget else None,
                       "provider_rate_estimate_usd": round(sum(item["estimated_cost_usd"] or 0.0 for item in summaries.values()), 8) if run_budget else None,
                       "stopped_before_overrun": bool(budget_stop_reason), "stop_reason": budget_stop_reason,
                       "ledger": str(run_budget.ledger_path) if run_budget else None},
        "protocol_fault_probes": protocol_fault_probes(),
        "notes": ["All data are synthetic and template-derived.", "No model result is used to train or tune on this run.", "Estimated costs use published DeepSeek peak/off-peak rates; other providers return null."],
    }
    if run_budget:
        run_budget._append({"kind": "run_summary", "run_id": run_budget.run_id, "benchmark": result["evaluation"], "split": split,
            "cases_evaluated": len(ids), "strategies": list(strategies),
            "provider_rate_estimate_usd": result["run_budget"]["provider_rate_estimate_usd"],
            "peak_rate_estimate_usd": result["run_budget"]["peak_rate_estimate_usd"],
            "cumulative_peak_estimate_usd": result["run_budget"]["cumulative_peak_estimate_usd"],
            "limit_usd": max_cost_usd, "stopped_before_overrun": bool(budget_stop_reason)})
    return result


def summarize(cases: list[dict[str, Any]], rates: dict[str, Any] | None = None) -> dict[str, Any]:
    decided = [case for case in cases if case["truth"]["decidable"]]
    unknown = [case for case in cases if not case["truth"]["decidable"]]
    single_root_cases = [case for case in decided if len(case["truth"].get("active_causes", case["truth"].get("primary_causes", [case["truth"]["category"]]))) == 1]
    single_root_correct = sum(case.get("prediction", {}).get("root_cause") in case["truth"].get("active_causes", [case["truth"]["category"]]) for case in single_root_cases)
    root_active_hits = 0
    top_k_hits = 0
    active_recalled = 0
    active_total = 0
    active_exact = 0
    for case in decided:
        truth = case["truth"]
        primary = set(truth.get("primary_causes", [truth["category"]]))
        active = set(truth.get("active_causes", truth.get("primary_causes", [truth["category"]])))
        ranked = case.get("prediction", {}).get("ranked_causes", [])
        if not ranked and case.get("prediction", {}).get("root_cause"):
            ranked = [case["prediction"]["root_cause"]]
        top = ranked[:3]
        root_active_hits += case.get("prediction", {}).get("root_cause") in active
        top_k_hits += bool(active.intersection(top))
        active_recalled += len(active.intersection(top))
        active_total += len(active)
        active_exact += set(top[:len(active)]) == active
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
    model_calls = sum(case.get("model", {}).get("calls", 0) for case in cases)
    cost = 0.0 if model_calls == 0 else estimate_cost(model, inputs, cached, outputs, rates)
    ordered = sorted(durations)
    p95 = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)] if ordered else None
    return {
        "single_root_accuracy": {"correct": single_root_correct, "total_single_root": len(single_root_cases), "rate": single_root_correct / len(single_root_cases) if single_root_cases else None},
        "active_cause_hit_at_1": {"correct": root_active_hits, "total_decidable": len(decided), "rate": root_active_hits / len(decided) if decided else None},
        "active_cause_hit_at_3": {"correct": top_k_hits, "total_decidable": len(decided), "rate": top_k_hits / len(decided) if decided else None},
        "active_cause_recall_at_3": {"recalled": active_recalled, "total_active_causes": active_total, "rate": active_recalled / active_total if active_total else None},
        "active_cause_exact_set": {"correct": active_exact, "total_decidable": len(decided), "rate": active_exact / len(decided) if decided else None},
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
                          "expert_handoffs": sum(sum(event.get("event") == "handoff_received" for event in case.get("raw_result", {}).get("trace", {}).get("events", [])) for case in cases),
                          "a2a_sends": sum(sum(event.get("method") == "message/send" for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "a2a_protocol") for case in cases),
                          "a2a_polls": sum(sum(event.get("method") == "tasks/get" for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "a2a_protocol") for case in cases),
                          "completed_handoffs": sum(sum(event.get("state") == "completed" for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "a2a_protocol") for case in cases),
                          "a2a_retries": sum(sum(event.get("count", 0) for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "transport_retries") for case in cases),
                          "cancellations": sum(sum(event.get("method") == "tasks/cancel" for event in case.get("raw_result", {}).get("trace", {}).get("events", []) if event.get("event") == "a2a_protocol") for case in cases)},
        "latency_ms": {"mean": round(statistics.mean(durations), 1) if durations else None, "p95": round(p95, 1) if p95 is not None else None},
        "token_usage": {"input": inputs, "cached_input": cached, "output": outputs, "total": inputs + outputs},
        "estimated_cost_usd": cost,
        "model_calls": model_calls,
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# OpsPilot 冻结集策略盲测评估", "",
        f"数据集 `{result['evaluation']}`；冻结测试集 {result['cases_evaluated']}/{result['test_cases_total']} 例；合成数据={result['manifest'].get('synthetic', True)}。运行时间：{result['created_at_utc']}。", "",
        "| 方法 | 单根因准确率 | 活动根因 Top-1 | 活动根因 Top-3 | 多根因召回 | 多根因精确集 | 弃答召回 | 错误确定 | 金证据 P/R | 引用存在 | 平均延迟 | P95 | 输入/输出 tokens | 估算费用 USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, item in result["summaries"].items():
        accuracy = item["single_root_accuracy"]
        abstain = item["insufficient_evidence_recall"]
        precision, recall = item["gold_evidence_precision"], item["gold_evidence_recall"]
        latency, tokens = item["latency_ms"], item["token_usage"]
        precision_text = f"{precision:.1%}" if precision is not None else "N/A"
        recall_text = f"{recall:.1%}" if recall is not None else "N/A"
        top1 = item["active_cause_hit_at_1"]
        top3 = item["active_cause_hit_at_3"]
        active = item["active_cause_recall_at_3"]
        exact = item["active_cause_exact_set"]
        lines.append(f"| {strategy} | {accuracy['correct']}/{accuracy['total_single_root']} ({accuracy['rate']:.1%}) | {top1['correct']}/{top1['total_decidable']} ({top1['rate']:.1%}) | {top3['correct']}/{top3['total_decidable']} ({top3['rate']:.1%}) | {active['recalled']}/{active['total_active_causes']} ({active['rate']:.1%}) | {exact['correct']}/{exact['total_decidable']} ({exact['rate']:.1%}) | {abstain['correct_abstentions']}/{abstain['total_insufficient']} ({abstain['rate']:.1%}) | {item['false_certainty']} | {precision_text}/{recall_text} | {item['citation_existence_cases']['valid']}/{item['citation_existence_cases']['total_decidable']} | {latency['mean']} ms | {latency['p95']} ms | {tokens['input']}/{tokens['output']} | {item['estimated_cost_usd']} |")
    probe = result["protocol_fault_probes"]
    safety = {name: item["action_safety"] for name, item in result["summaries"].items()}
    lines.extend(["", f"A2A 故障探针：重试恢复 `{probe['transient_retry_recovered']}`；重复消息幂等 `{probe['duplicate_message_idempotent']}`；截止后取消 `{probe['deadline_cancellation']}`。", "",
                  "A2A 实际交接（按策略）：" + "；".join(f"{name}: 专家交接 {item['communication']['expert_handoffs']}、message/send {item['communication']['a2a_sends']}、tasks/get {item['communication']['a2a_polls']}、取消 {item['communication']['cancellations']}、失败 {item['communication']['failed_cases']}" for name, item in result["summaries"].items()) + ".", "",
                  f"审批标记：模型曾漏标建议（规则/单 Agent/多 Agent）={safety.get('rules', {}).get('model_underapproved_suggestions', 0)}/{safety.get('single-agent', {}).get('model_underapproved_suggestions', 0)}/{safety.get('multi-agent', {}).get('model_underapproved_suggestions', 0)}；应用闸门均补为需审批。", "",
                  "费用采用 DeepSeek 官方价格快照和实际时段、缓存 token 估算。样本量小，不能代表生产表现。", "",
                  "逐例轨迹与原始响应见同名 JSON 文件。"])
    if result.get("auto_route_distribution"):
        lines.extend(["", "Auto 路由：" + "；".join(f"{route} {count} 例" for route, count in sorted(result["auto_route_distribution"].items())) + "。"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare rules, single-agent and multi-agent on frozen blind test data")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--strategy", choices=("rules", "single-agent", "multi-agent", "auto"), action="append")
    parser.add_argument("--benchmark-dir", type=Path, default=ROOT / "data" / "benchmark")
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "agent-evaluation.json")
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--cost-ledger", type=Path, default=ROOT / "reports" / "api-cost-ledger.jsonl")
    args = parser.parse_args()
    result = evaluate(args.limit, tuple(args.strategy or ("rules", "single-agent", "multi-agent")), args.benchmark_dir, args.split,
                      args.max_cost_usd, args.cost_ledger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps(result["summaries"], ensure_ascii=False, indent=2))
    print(f"output={args.output}")
    return 0 if result["protocol_fault_probes"]["passed"] and all(summary["communication"]["failed_cases"] == 0 or strategy == "rules" for strategy, summary in result["summaries"].items()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
