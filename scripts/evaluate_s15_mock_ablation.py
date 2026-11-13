"""Offline paired ablation of S15 routes with deterministic, non-agent mocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.cli import load_incidents  # noqa: E402
from opspilot.investigator import investigate  # noqa: E402
from opspilot.routing import select_route  # noqa: E402

STRATEGIES = ("rules", "single", "always-multi", "auto")
FROZEN_TEST_HASHES = {
    "data_sha256": "78d0b6576a39ac7c8475442511b01d70707014113b014da2d01c95fa3f940efd",
    "labels_sha256": "223bc0e4c9f4d61f838da36324b2477c8eb97caf8c08ee38ee299bf52c5df9e1",
}
CAUSE_NAMES = {
    "database connection pool exhausted": "pool_exhaustion",
    "deployment introduced invalid order service configuration": "config_error",
    "downstream service exceeded request timeout": "downstream_timeout",
}


def _load_frozen_cases(benchmark_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    manifest = json.loads((benchmark_dir / "manifest.json").read_text(encoding="utf-8"))
    split_manifest = manifest["splits"]["test"]
    data_path = benchmark_dir / "test.jsonl"
    labels_path = benchmark_dir / "labels" / "test.json"
    actual_hashes = {
        "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
    }
    if actual_hashes != FROZEN_TEST_HASHES or any(split_manifest.get(key) != value for key, value in actual_hashes.items()):
        raise ValueError("frozen S15 test inputs changed; refusing paired comparison")
    incidents = load_incidents(data_path)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    if set(incidents) != set(label_by_id):
        raise ValueError("frozen incidents and independent truth labels do not match")
    return incidents, label_by_id, {"manifest": manifest, **actual_hashes}


def _candidate_predictions(report: dict[str, Any], available: set[str], limit: int) -> tuple[list[str], list[str]]:
    ranked: list[str] = []
    evidence: list[str] = []
    for item in report.get("hypotheses", []):
        cause = item.get("hypothesis_id")
        support = [ref for ref in item.get("strong_support", []) if ref in available]
        if isinstance(cause, str) and support and cause not in ranked:
            ranked.append(cause)
            evidence.extend(ref for ref in support if ref not in evidence)
            if len(ranked) == limit:
                break
    return ranked, evidence


def _predict(strategy: str, report: dict[str, Any], available: set[str]) -> tuple[list[str], list[str], str | None]:
    selected_route = None
    if strategy == "rules":
        conclusion = CAUSE_NAMES.get(report.get("conclusion"))
        if not conclusion:
            return [], [], None
        hypothesis = next((item for item in report.get("hypotheses", []) if item.get("hypothesis_id") == conclusion), {})
        evidence = [ref for ref in hypothesis.get("support", []) if ref in available]
        return [conclusion], evidence, None
    if strategy == "single":
        ranked, evidence = _candidate_predictions(report, available, 1)
        return ranked, evidence, "single"
    if strategy == "always-multi":
        ranked, evidence = _candidate_predictions(report, available, 3)
        return ranked, evidence, "multi"
    if strategy == "auto":
        selected_route, _ = select_route(report)
        if selected_route == "rules":
            ranked, evidence, _ = _predict("rules", report, available)
            return ranked, evidence, selected_route
        ranked, evidence = _candidate_predictions(report, available, 3 if selected_route == "multi-agent" else 1)
        return ranked, evidence, selected_route
    raise ValueError(f"unknown strategy: {strategy}")


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    decided = [case for case in cases if case["truth"]["decidable"]]
    insufficient = [case for case in cases if not case["truth"]["decidable"]]
    exact = top1 = top3 = recalled = active_total = 0
    false_positive_causes = predicted_causes = false_positive_cases = 0
    gold: set[str] = set()
    cited: set[str] = set()
    for case in decided:
        active = set(case["truth"].get("active_causes", []))
        predicted = case["prediction"]["root_causes"]
        exact += set(predicted) == active
        top1 += bool(predicted and predicted[0] in active)
        top3 += bool(set(predicted[:3]) & active)
        recalled += len(set(predicted[:3]) & active)
        active_total += len(active)
        false = set(predicted) - active
        false_positive_causes += len(false)
        predicted_causes += len(predicted)
        false_positive_cases += bool(false)
        gold.update(case["truth"].get("gold_evidence_ids", []))
        cited.update(case["prediction"]["evidence_ids"])
    latencies = [case["duration_ms"] for case in cases]
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)] if ordered else None
    return {
        "decidable_cases": len(decided),
        "root_cause_set_exact": {"correct": exact, "total": len(decided), "rate": exact / len(decided) if decided else None},
        "top1_active_cause": {"correct": top1, "total": len(decided), "rate": top1 / len(decided) if decided else None},
        "top3_active_cause": {"correct": top3, "total": len(decided), "rate": top3 / len(decided) if decided else None},
        "active_cause_recall_at_3": {"recalled": recalled, "total": active_total, "rate": recalled / active_total if active_total else None},
        "false_positives": {"causes": false_positive_causes, "cases": false_positive_cases,
                            "cause_rate": false_positive_causes / predicted_causes if predicted_causes else 0.0},
        "false_certainty": sum(bool(case["prediction"]["root_causes"]) for case in insufficient),
        "insufficient_evidence_abstentions": sum(not case["prediction"]["root_causes"] for case in insufficient),
        "evidence_precision": len(cited & gold) / len(cited) if cited else None,
        "evidence_recall": len(cited & gold) / len(gold) if gold else None,
        "estimated_cost_usd": 0.0,
        "model_calls": 0,
        "latency_ms": {"mean_local_runner": round(statistics.mean(latencies), 3) if latencies else None,
                       "p95_local_runner": round(p95, 3) if p95 is not None else None},
        "cancellations": sum(case["status"] == "cancelled" for case in cases),
        "failures": sum(case["status"] == "failed" for case in cases),
    }


def evaluate(benchmark_dir: Path | None = None, injected_faults: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
    data_dir = benchmark_dir or ROOT / "data" / "causal-benchmark"
    incidents, label_by_id, frozen = _load_frozen_cases(data_dir)
    faults = injected_faults or {}
    if any(strategy not in STRATEGIES or incident_id not in incidents or fault not in {"cancel", "failure"}
           for (strategy, incident_id), fault in faults.items()):
        raise ValueError("mock faults require a known strategy, frozen incident ID, and cancel/failure outcome")
    paired: dict[str, list[dict[str, Any]]] = {strategy: [] for strategy in STRATEGIES}
    failed_samples: list[dict[str, Any]] = []
    for incident_id in sorted(incidents):
        incident = incidents[incident_id]
        label = label_by_id[incident_id]
        truth = {"decidable": label["decidable"], "active_causes": label.get("active_causes", []),
                 "gold_evidence_ids": label.get("gold_evidence_ids", [])}
        report = investigate(incident, query_budget=8)
        available = {row["record_id"] for records in incident["signals"].values() for row in records}
        for strategy in STRATEGIES:
            started = time.perf_counter()
            fault = faults.get((strategy, incident_id))
            if fault in {"cancel", "failure"}:
                case = {"incident_id": incident_id, "status": "cancelled" if fault == "cancel" else "failed",
                        "truth": truth, "prediction": {"root_causes": [], "evidence_ids": []},
                        "selected_route": None, "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "failure_reason": f"injected_{fault}"}
            else:
                predicted, evidence, selected_route = _predict(strategy, report, available)
                case = {"incident_id": incident_id, "status": "completed", "truth": truth,
                        "prediction": {"root_causes": predicted, "evidence_ids": evidence},
                        "selected_route": selected_route,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3)}
            paired[strategy].append(case)
            if case["status"] != "completed":
                failure_reason = "mock_cancelled" if case["status"] == "cancelled" else "mock_execution_failure"
            elif truth["decidable"] and set(case["prediction"]["root_causes"]) != set(truth.get("active_causes", [])):
                failure_reason = "root_cause_set_mismatch"
            elif not truth["decidable"] and case["prediction"]["root_causes"]:
                failure_reason = "false_certainty"
            else:
                failure_reason = None
            if failure_reason:
                failed_samples.append({"strategy": strategy, "incident_id": incident_id, "status": case["status"],
                                      "failure_reason": failure_reason,
                                      "truth": {"decidable": truth["decidable"], "active_causes": truth["active_causes"]},
                                      "prediction": case["prediction"], "selected_route": case["selected_route"]})
    expected_ids = set(incidents)
    if any({case["incident_id"] for case in cases} != expected_ids for cases in paired.values()):
        raise RuntimeError("paired strategies did not evaluate identical frozen cases")
    return {
        "evaluation": "s15_offline_mock_paired_ablation_v1",
        "runner": "deterministic_mock_no_model_calls",
        "benchmark": frozen,
        "strategies": list(STRATEGIES),
        "case_count_per_strategy": len(incidents),
        "metric_definitions": {
            "root_cause_set_exact": "predicted cause set equals independent active_causes, among decidable cases",
            "top1_active_cause": "first predicted cause belongs to active_causes, among decidable cases",
            "top3_active_cause": "at least one active cause appears in first three predictions, among decidable cases",
            "active_cause_recall_at_3": "micro recall over all independent active causes, among decidable cases",
            "false_positives": "predicted causes outside active_causes; cause_rate denominator is all predicted causes",
            "false_certainty": "number of insufficient-evidence cases with any predicted cause",
            "insufficient_evidence_abstentions": "number of insufficient-evidence cases with no predicted cause",
            "evidence_precision_recall": "micro set precision/recall against independent gold_evidence_ids, among decidable cases",
            "estimated_cost_usd": "always zero: runner makes no model/provider calls; not an agent cost estimate",
            "latency_ms": "local deterministic runner wall time; not model or end-to-end latency",
            "cancellations_failures": "count of explicitly injected mock outcomes; default run injects neither",
        },
        "summaries": {strategy: _summarize(cases) for strategy, cases in paired.items()},
        "cases": paired,
        "failure_samples": failed_samples,
        "interpretation": "Harness mechanics and route-policy behavior only. Mock predictions are not Agent outputs or evidence of model quality.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline deterministic mock paired ablation for S15")
    parser.add_argument("--benchmark-dir", type=Path, default=ROOT / "data" / "causal-benchmark")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "s15-offline-mock-ablation.json")
    parser.add_argument("--failures-output", type=Path, default=ROOT / "reports" / "s15-offline-mock-ablation-failures.json")
    parser.add_argument("--inject-cancel", action="append", default=[], metavar="STRATEGY:INCIDENT_ID")
    parser.add_argument("--inject-failure", action="append", default=[], metavar="STRATEGY:INCIDENT_ID")
    args = parser.parse_args()
    faults = {}
    for value in args.inject_cancel:
        strategy, separator, incident_id = value.partition(":")
        if not separator or strategy not in STRATEGIES:
            parser.error("--inject-cancel must be STRATEGY:INCIDENT_ID")
        faults[(strategy, incident_id)] = "cancel"
    for value in args.inject_failure:
        strategy, separator, incident_id = value.partition(":")
        if not separator or strategy not in STRATEGIES:
            parser.error("--inject-failure must be STRATEGY:INCIDENT_ID")
        faults[(strategy, incident_id)] = "failure"
    result = evaluate(args.benchmark_dir, faults)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {key: value for key, value in result.items() if key not in {"cases", "failure_samples"}}
    report["failure_sample_count"] = len(result["failure_samples"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.failures_output.parent.mkdir(parents=True, exist_ok=True)
    args.failures_output.write_text(json.dumps(result["failure_samples"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_count_per_strategy": result["case_count_per_strategy"], "summaries": result["summaries"],
                      "failure_samples": len(result["failure_samples"])}, ensure_ascii=False, indent=2))
    print(f"output={args.output}")
    print(f"failures_output={args.failures_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
