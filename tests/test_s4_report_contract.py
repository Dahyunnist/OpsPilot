import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_s4_report_counts_abstentions_and_validates_evidence_against_baseline():
    report = json.loads((ROOT / "reports/evaluation.json").read_text(encoding="utf-8"))
    assert report["dataset_cases"] == 8
    assert report["runs_per_case"] == 2
    assert report["stability_check"]["all_decision_signatures_match"] is True
    metrics = report["metrics"]
    assert metrics["top1_root_cause"] == {"correct": 6, "total": 6, "accuracy": 1.0}
    assert metrics["insufficient_evidence_recall"] == {"correct_abstentions": 2, "total_insufficient": 2, "recall": 1.0}
    assert metrics["evidence_reference_validity"] == {"valid": 32, "total": 32, "rate": 1.0}
    assert metrics["logs_only_top1"]["total"] == 6
    assert metrics["logs_only_top1"]["correct"] < metrics["top1_root_cause"]["correct"]
    assert report["evaluation"] == "synthetic_s0_s4_deterministic_rules"
