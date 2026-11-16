import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.audit_op3_partial_run import build_public_summary


def _metric_row(*, completed: bool, correct: bool, failure: str | None = None) -> dict:
    metrics = {"exact_root_set": correct, "top1": correct, "top3_recall": 1.0 if correct else 0.0,
        "false_positives": 0, "evidence_true_positive": 1 if correct else 0,
        "evidence_predicted": 1, "evidence_gold": 1}
    return {"duration_ms": 12, "model_calls": 1, "provider_rate_estimate_usd": 0.001,
        "evaluation_status": "complete" if completed else "failed",
        "failures": [] if completed else [failure or "ModelError"], "metrics": metrics,
        "selected_route": "single", "fallback_route": None}


def _report() -> dict:
    rows = []
    case_ids = ("normal_slow_sql", "missing_pool_evidence", "contradictory_payment")
    completed = {"rules": set(case_ids), "single": set(case_ids[:2]), "multi": {case_ids[0]},
        "auto": set(case_ids[:2])}
    for case_id in case_ids:
        strategies = {name: _metric_row(completed=case_id in completed[name],
            correct=case_id == case_ids[0], failure="BudgetLimitExceeded") for name in
            ("rules", "single", "multi", "auto")}
        strategies["auto"]["selected_route"] = "rules" if case_id == case_ids[0] else "single"
        rows.append({"case_id": case_id, "strategies": strategies})
    return {"benchmark": "op2_model_paired_subset", "run_id": "offline-fixture",
        "evaluation_status": "partial", "per_case": rows,
        "budget": {"provider_request_attempts": 17, "max_provider_requests": 18,
            "actual_run_peak_usd": 0.014, "preregistered_worst_case_usd": 0.0230226,
            "prior_ledger_peak_usd": 1.5, "remaining_usd": 0.1},
        "provider_preflight": {"balance_infos": [{"total_balance": 999}]},
        "request_payload_audit": [{"messages": [{"content": "private prompt"}], "api_key": "secret"}]}


class PublicOp3SummaryTests(unittest.TestCase):
    def test_rebuild_counts_failures_and_emits_allowlisted_fields_only(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "raw.json"
            source.write_text(json.dumps(_report()), encoding="utf-8")
            public = build_public_summary(source)

        summary = public["strategy_summary"]
        self.assertEqual({"rules": 1.0, "single": 2 / 3, "multi": 1 / 3, "auto": 2 / 3},
            {name: item["evaluation_coverage_rate"] for name, item in summary.items()})
        self.assertEqual({name: 1 / 3 for name in summary},
            {name: item["top1_rate"] for name, item in summary.items()})
        self.assertEqual(["BudgetLimitExceeded"], summary["multi"]["failures"][0]["reasons"])
        self.assertEqual(1.0, summary["multi"]["top1_rate_completed_only"])
        self.assertEqual(1, public["latency_sample_counts"]["multi"]["included_completed_cases"])
        self.assertEqual(2, public["latency_sample_counts"]["multi"]["failed_cases_without_latency"])
        self.assertIn("provider-only p95 is unavailable", public["measurement_notes"]["latency"])
        serialized = json.dumps(public)
        for forbidden in ("prior_ledger_peak_usd", "remaining_usd", "balance_infos", "api_key",
                "private prompt", "request_payload_audit"):
            self.assertNotIn(forbidden, serialized)

    def test_rebuild_rejects_expanded_case_scope_and_excess_request_limit(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "raw.json"
            report = _report()
            report["per_case"].append({"case_id": "new-case", "strategies": {}})
            source.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact three frozen cases"):
                build_public_summary(source)

            report = _report()
            report["budget"]["provider_request_attempts"] = 19
            source.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved bound"):
                build_public_summary(source)


if __name__ == "__main__":
    unittest.main()
