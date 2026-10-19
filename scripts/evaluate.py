"""Run the fixed synthetic incident evaluation twice and save raw reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.cli import load_incidents  # noqa: E402
from opspilot.investigator import investigate  # noqa: E402

ACTION_FOR_CATEGORY = {
    "pool_exhaustion": "restart_order",
    "config_error": "rollback_order_config",
    "downstream_timeout": "restart_downstream",
}


def stable_signature(report: dict[str, Any]) -> str:
    stable = {
        "incident_id": report["incident_id"],
        "status": report["status"],
        "conclusion": report["conclusion"],
        "hypotheses": [
            {key: item[key] for key in ("hypothesis_id", "support", "against", "score", "disposition")}
            for item in report["hypotheses"]
        ],
        "query_strategy": report["query_strategy"],
        "comparisons": report["comparison"],
        "correlations": report["correlations"],
        "recommendation": report["recommendation"],
        "queries": [
            {"source": query["source"], "status": query["status"], "params": query["params"], "record_ids": [row["record_id"] for row in query["records"]]}
            for query in report["query_trace"]
        ],
    }
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate() -> dict[str, Any]:
    incidents = load_incidents(ROOT / "data" / "incidents.jsonl")
    labels = json.loads((ROOT / "data" / "labels.json").read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    if set(incidents) != set(label_by_id):
        raise ValueError("incident IDs and label IDs do not match")

    runs = [{incident_id: investigate(incident) for incident_id, incident in sorted(incidents.items())} for _ in range(2)]
    decidable = [item for item in labels if item["decidable"]]
    unknown = [item for item in labels if not item["decidable"]]
    full_correct = sum(runs[0][item["incident_id"]]["conclusion"] == item["root_cause"] for item in decidable)
    full_abstained = sum(runs[0][item["incident_id"]]["status"] == "insufficient_evidence" for item in unknown)
    logs_correct = sum(runs[0][item["incident_id"]]["comparison"]["logs_only"]["conclusion"] == item["root_cause"] for item in decidable)
    no_refutation_correct = sum(runs[0][item["incident_id"]]["comparison"]["no_refutation"]["conclusion"] == item["root_cause"] for item in decidable)
    no_refutation_competitors = sum(len(report["comparison"]["no_refutation"]["supported_competitors"]) for report in runs[0].values())
    citations = [
        (report, hypothesis, evidence_id)
        for report in runs[0].values()
        for hypothesis in report["hypotheses"]
        for evidence_id in hypothesis["support"] + hypothesis["against"]
    ]
    valid_citations = sum(
        report["citation_validation"]["all_references_exist"]
        and any(row["record_id"] == evidence_id for query in report["query_trace"] for row in query["records"])
        for report, _hypothesis, evidence_id in citations
    )
    incorrect_actions = 0
    recommendation_count = 0
    executed_unapproved = 0
    for label in labels:
        report = runs[0][label["incident_id"]]
        recommendation = report["recommendation"]
        expected_action = ACTION_FOR_CATEGORY.get(label["category"])
        if recommendation:
            recommendation_count += 1
        if (recommendation or {}).get("action_id") != expected_action:
            incorrect_actions += 1
        if recommendation and not recommendation["requires_approval"]:
            executed_unapproved += 1
    signatures_match = all(stable_signature(runs[0][key]) == stable_signature(runs[1][key]) for key in incidents)
    per_case = []
    for incident_id in sorted(incidents):
        report = runs[0][incident_id]
        label = label_by_id[incident_id]
        per_case.append({
            "incident_id": incident_id,
            "truth": label,
            "prediction": {"status": report["status"], "conclusion": report["conclusion"]},
            "logs_only_prediction": report["comparison"]["logs_only"],
            "recommendation": report["recommendation"],
            "query_count": report["cost"]["query_count"],
            "duration_ms": report["cost"]["duration_ms"],
            "input_tokens": report["cost"]["input_tokens"],
            "output_tokens": report["cost"]["output_tokens"],
            "citation_validation": report["citation_validation"],
            "report": report,
            "rerun_signature": stable_signature(runs[1][incident_id]),
        })
    report_payload = {
        "evaluation": "synthetic_s0_s4_deterministic_rules",
        "dataset_cases": len(labels),
        "runs_per_case": 2,
        "stability_check": {"all_decision_signatures_match": signatures_match},
        "metrics": {
            "top1_root_cause": {"correct": full_correct, "total": len(decidable), "accuracy": full_correct / len(decidable) if decidable else None},
            "insufficient_evidence_recall": {"correct_abstentions": full_abstained, "total_insufficient": len(unknown), "recall": full_abstained / len(unknown) if unknown else None},
            "logs_only_top1": {"correct": logs_correct, "total": len(decidable), "accuracy": logs_correct / len(decidable) if decidable else None},
            "no_refutation_top1": {"correct": no_refutation_correct, "total": len(decidable), "accuracy": no_refutation_correct / len(decidable) if decidable else None},
            "no_refutation_supported_competitors": no_refutation_competitors,
            "evidence_reference_validity": {"valid": valid_citations, "total": len(citations), "rate": valid_citations / len(citations) if citations else 0.0},
            "incorrect_action_recommendation_rate": {"incorrect": incorrect_actions, "total_recommendations": recommendation_count, "rate": incorrect_actions / recommendation_count if recommendation_count else None},
            "unapproved_recommendations": {"count": executed_unapproved, "total_recommendations": recommendation_count},
            "uncertainty_calibration": {
                "probability_calibration": "not_applicable; deterministic rules emit no probabilities",
                "false_abstentions": len(decidable) - sum(runs[0][item["incident_id"]]["status"] == "determined" for item in decidable),
                "false_certainty": len(unknown) - full_abstained,
            },
            "investigation_cost": {
                "average_queries": mean(case["query_count"] for case in per_case),
                "average_duration_ms": round(mean(case["duration_ms"] for case in per_case), 3),
                "average_input_tokens": None,
                "average_output_tokens": None,
                "token_note": "not_applicable; no model calls",
            },
        },
        "cases": per_case,
    }
    return report_payload


def markdown_summary(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    return "\n".join([
        "# OpsPilot 固定合成集评测",
        "",
        f"案例数：{result['dataset_cases']}；每例运行：{result['runs_per_case']} 次。",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 多信号 Top1 根因准确率 | {metrics['top1_root_cause']['correct']}/{metrics['top1_root_cause']['total']} ({metrics['top1_root_cause']['accuracy']:.1%}) |",
        f"| 证据不足召回率 | {metrics['insufficient_evidence_recall']['correct_abstentions']}/{metrics['insufficient_evidence_recall']['total_insufficient']} ({metrics['insufficient_evidence_recall']['recall']:.1%}) |",
        f"| 仅日志 Top1 准确率 | {metrics['logs_only_top1']['correct']}/{metrics['logs_only_top1']['total']} ({metrics['logs_only_top1']['accuracy']:.1%}) |",
        f"| 无反证策略 Top1 准确率 | {metrics['no_refutation_top1']['correct']}/{metrics['no_refutation_top1']['total']} ({metrics['no_refutation_top1']['accuracy']:.1%}) |",
        f"| 无反证遗留竞争假设 | {metrics['no_refutation_supported_competitors']} 条 |",
        f"| 引用存在性 | {metrics['evidence_reference_validity']['valid']}/{metrics['evidence_reference_validity']['total']} ({metrics['evidence_reference_validity']['rate']:.1%}) |",
        f"| 错误行动建议率 | {metrics['incorrect_action_recommendation_rate']['incorrect']}/{metrics['incorrect_action_recommendation_rate']['total_recommendations']} ({metrics['incorrect_action_recommendation_rate']['rate']:.1%}) |",
        f"| 平均查询数 | {metrics['investigation_cost']['average_queries']:.2f} |",
        f"| 平均本地耗时 | {metrics['investigation_cost']['average_duration_ms']:.3f} ms |",
        "| Token | 不适用：没有模型调用 |",
        "",
        "评测使用固定种子的合成数据与确定性规则，不代表模型或生产表现。概率校准不适用；报告假阳性确定和错误弃答数。逐例完整报告见同目录 `evaluation.json`。",
        "",
        f"决策重跑一致：`{result['stability_check']['all_decision_signatures_match']}`。",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    result = evaluate()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "evaluation.md").write_text(markdown_summary(result), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"stable={result['stability_check']['all_decision_signatures_match']} cases={result['dataset_cases']} output={args.output_dir}")
    return 0 if result["stability_check"]["all_decision_signatures_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
