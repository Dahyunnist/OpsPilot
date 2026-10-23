from __future__ import annotations

import json
import threading
import unittest

from opspilot.strategy_eval import STRATEGIES, evaluate_snapshot, score_case, summarize_cases
from opspilot.model_client import ModelError
from scripts.evaluate_agents import BudgetLimitExceeded


START = "2026-10-23T10:00:00Z"
END = "2026-10-23T10:10:00Z"


class FakeChatModel:
    model = "deepseek-v4-flash"
    max_tokens = 512

    def __init__(self) -> None:
        self.calls = []

    def complete(self, system: str, user: str, _budget: int) -> dict:
        payload = json.loads(user)
        self.calls.append((system, payload))
        if system.startswith("You are an independent "):
            source = next(name for name in ("metrics", "logs", "traces", "deployments") if f"independent {name} telemetry expert" in system)
            record_id = next(row["record_id"] for row in payload["records"])
            result = {"hypotheses": [{"id": "database_pool_exhaustion", "evidence_ids": [record_id], "reason": "source signal"}]}
        else:
            result = {"root_causes": ["database_pool_exhaustion"],
                "evidence_by_root": {"database_pool_exhaustion": ["m1", "l1"]}, "uncertainty": "none"}
        return {"data": result, "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 0}}}


def rec(source: str, record_id: str, timestamp: str, service: str, attrs: dict, raw: dict | None = None) -> dict:
    return {"source": source, "record_id": record_id, "timestamp": timestamp, "service": service,
        "trace_id": None, "attributes": attrs, "raw": raw or {}}


class StrategyEvaluationTests(unittest.TestCase):
    def snapshot(self) -> dict:
        return {
            "metrics": {"records": [
                rec("metrics", "m0", "2026-10-23T10:01:00Z", "orders", {"name": "opspilot_http_errors_total", "version": "v1", "value": 0}),
                rec("metrics", "m1", "2026-10-23T10:09:00Z", "orders", {"name": "opspilot_http_errors_total", "version": "v1", "value": 2}),
            ]},
            "logs": {"records": [rec("logs", "l1", "2026-10-23T10:05:00Z", "orders", {},
                {"message": "database sessions remain occupied", "timestamp": "2026-10-23T10:05:00Z", "attributes": {}})]},
            "traces": {"records": []}, "deployments": {"records": []},
        }

    def test_four_strategies_use_one_snapshot_and_ground_model_claims(self) -> None:
        model = FakeChatModel()

        def gate(operation, instance, system, user, budget):
            return operation(instance, system, user, budget)

        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), model, gate)
        report = score_case(report, ["database_pool_exhaustion"], {"m1", "l1"})
        self.assertEqual(set(report["strategies"]), set(STRATEGIES))
        self.assertEqual(len({report["snapshot_sha256"]}), 1)
        self.assertEqual(report["strategies"]["rules"]["metrics"]["exact_root_set"], True)
        self.assertEqual(report["strategies"]["single"]["metrics"]["exact_root_set"], True)
        self.assertEqual(report["strategies"]["multi"]["metrics"]["exact_root_set"], True)
        self.assertEqual(report["strategies"]["single"]["model_calls"], 1)
        self.assertEqual(report["strategies"]["multi"]["model_calls"], 3)
        self.assertEqual(report["strategies"]["auto"]["model_calls"], 0)
        self.assertGreater(report["strategies"]["multi"]["provider_rate_estimate_usd"], 0)
        self.assertTrue(all("lab-pool-01" not in json.dumps(call[1]) for call in model.calls))

    def test_ungrounded_or_single_source_model_claim_is_withheld(self) -> None:
        class GuessModel(FakeChatModel):
            def complete(self, system: str, user: str, _budget: int) -> dict:
                if system.startswith("You are an independent "):
                    return {"data": {"hypotheses": []}, "usage": {}}
                return {"data": {"root_causes": ["slow_database_query"],
                    "evidence_by_root": {"slow_database_query": ["invented-ref"]}}, "usage": {}}

        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), GuessModel(),
            lambda operation, instance, system, user, budget: operation(instance, system, user, budget), ("single",))
        self.assertEqual(report["strategies"]["single"]["prediction"]["root_causes"], [])
        self.assertEqual(report["strategies"]["single"]["prediction"]["withheld_claims"], ["slow_database_query"])

    def test_multi_failure_falls_back_to_rules_and_is_not_scored_as_multi_success(self) -> None:
        class OneExpertFails(FakeChatModel):
            def complete(self, system: str, user: str, budget: int) -> dict:
                if "independent metrics telemetry expert" in system:
                    raise ModelError("model request failed: HTTP 503", category="provider_http", code=503, retryable=True)
                return super().complete(system, user, budget)

        model = OneExpertFails()
        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), model,
            lambda operation, instance, system, user, budget: operation(instance, system, user, budget), ("multi",))
        report = score_case(report, ["database_pool_exhaustion"], {"m1", "l1"})
        multi = report["strategies"]["multi"]
        self.assertEqual(multi["failures"], ["provider_http:503"])
        self.assertEqual(multi["error_trace"], [{"category": "provider_http", "code": 503,
            "retryable": True, "http_status": 503}])
        self.assertEqual(multi["fallback_route"], "rules")
        self.assertEqual(multi["prediction"]["root_causes"], ["database_pool_exhaustion"])
        self.assertEqual(multi["model_calls"], 2)
        self.assertIsNone(multi["metrics"]["exact_root_set"])
        self.assertEqual(len(model.calls), 1)

    def test_adjudicator_failure_falls_back_without_claiming_multi_success(self) -> None:
        class AdjudicatorFails(FakeChatModel):
            def complete(self, system: str, user: str, budget: int) -> dict:
                if system.startswith("You are an adversarial incident adjudicator"):
                    raise ModelError("model returned invalid JSON (finish=length)", category="response_schema",
                        code="invalid_json_content")
                return super().complete(system, user, budget)

        model = AdjudicatorFails()
        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), model,
            lambda operation, instance, system, user, budget: operation(instance, system, user, budget), ("multi",))
        report = score_case(report, ["database_pool_exhaustion"], {"m1", "l1"})
        multi = report["strategies"]["multi"]
        self.assertEqual(multi["failures"], ["response_schema:invalid_json_content"])
        self.assertEqual(multi["error_trace"][0]["code"], "invalid_json_content")
        self.assertEqual(multi["fallback_route"], "rules")
        self.assertEqual(multi["evaluation_status"], "failed")
        self.assertEqual(multi["prediction"]["root_causes"], ["database_pool_exhaustion"])
        self.assertEqual(multi["model_calls"], 3)
        self.assertIsNone(multi["metrics"]["exact_root_set"])
        self.assertEqual(len(model.calls), 2)

    def test_truncated_specialists_get_one_budgeted_retry_then_fallback(self) -> None:
        class TruncatedExperts(FakeChatModel):
            def complete(self, system: str, user: str, budget: int) -> dict:
                if system.startswith("You are an independent "):
                    raise ModelError("truncated", category="response_schema", code="truncated_json",
                        retryable=True, finish_reason="length", completion_tokens=budget,
                        completion_token_budget=budget)
                return super().complete(system, user, budget)

        model = TruncatedExperts()
        budgets = []

        def gate(operation, instance, system, user, budget):
            budgets.append(budget)
            return operation(instance, system, user, budget)

        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), model, gate, ("multi",))
        multi = report["strategies"]["multi"]
        self.assertEqual(sorted(budgets), [320, 320, 512, 512])
        self.assertEqual(multi["model_calls"], 4)
        self.assertEqual(multi["fallback_route"], "rules")
        self.assertEqual(multi["evaluation_status"], "failed")
        self.assertTrue(all(trace["code"] == "truncated_json" for trace in multi["error_trace"]))

    def test_single_truncation_recovers_once_without_fallback(self) -> None:
        class TruncateOnce(FakeChatModel):
            def __init__(self):
                super().__init__()
                self.truncated = set()

            def complete(self, system: str, user: str, budget: int) -> dict:
                if system.startswith("You are an independent ") and "metrics" not in self.truncated:
                    self.truncated.add("metrics")
                    raise ModelError("truncated", category="response_schema", code="truncated_json",
                        retryable=True, finish_reason="length", completion_tokens=budget,
                        completion_token_budget=budget)
                return super().complete(system, user, budget)

        model = TruncateOnce()
        budgets = []
        lock = threading.Lock()

        def gate(operation, instance, system, user, budget):
            with lock:
                budgets.append(budget)
            return operation(instance, system, user, budget)

        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), model, gate, ("multi",))
        multi = report["strategies"]["multi"]
        self.assertEqual(sorted(budgets), [320, 320, 512, 512])
        self.assertEqual(multi["model_calls"], 4)
        self.assertEqual(multi["evaluation_status"], "complete")
        self.assertIsNone(multi["fallback_route"])

    def test_retry_budget_exhaustion_stops_before_call_and_falls_back(self) -> None:
        class TruncateExperts(FakeChatModel):
            def complete(self, system: str, user: str, budget: int) -> dict:
                if system.startswith("You are an independent "):
                    raise ModelError("truncated", category="response_schema", code="truncated_json",
                        retryable=True, finish_reason="length", completion_tokens=budget,
                        completion_token_budget=budget)
                return super().complete(system, user, budget)

        model = TruncateExperts()
        retries_denied = []
        lock = threading.Lock()

        def gate(operation, instance, system, user, budget):
            if budget > 320:
                with lock:
                    retries_denied.append(budget)
                raise BudgetLimitExceeded("ledger cap")
            return operation(instance, system, user, budget)

        report = evaluate_snapshot("lab-pool-01", START, END, self.snapshot(), model, gate, ("multi",))
        multi = report["strategies"]["multi"]
        self.assertEqual(retries_denied, [512, 512])
        self.assertEqual(multi["model_calls"], 2)
        self.assertEqual(multi["fallback_route"], "rules")
        self.assertEqual(multi["evaluation_status"], "failed")
        self.assertEqual(multi["failures"], ["BudgetLimitExceeded", "BudgetLimitExceeded"])

    def test_summary_excludes_failed_partial_predictions_from_performance(self) -> None:
        def row(*, failed: bool, exact: bool, duration: int) -> dict:
            return {"duration_ms": duration, "model_calls": 1, "provider_rate_estimate_usd": 0.1,
                "failures": ["ModelError"] if failed else [], "evaluation_status": "failed" if failed else "complete",
                "metrics": {"exact_root_set": exact, "top1": exact, "top3_recall": 1.0,
                    "false_positives": 0, "evidence_true_positive": 1, "evidence_predicted": 1, "evidence_gold": 1}}

        summary = summarize_cases([
            {"case_id": "complete", "strategies": {"multi": row(failed=False, exact=True, duration=100)}},
            {"case_id": "partial", "strategies": {"multi": row(failed=True, exact=True, duration=900)}},
        ])
        self.assertEqual(summary["multi"]["cases"], 2)
        self.assertEqual(summary["multi"]["completed_cases"], 1)
        self.assertEqual(summary["multi"]["failed_cases"], 1)
        self.assertEqual(summary["multi"]["exact_root_set_successes"], 1)
        self.assertEqual(summary["multi"]["p95_latency_ms"], 100)
        self.assertEqual(summary["multi"]["failures"][0]["case_id"], "partial")

        failed_only = summarize_cases([
            {"case_id": "partial", "strategies": {"multi": row(failed=True, exact=True, duration=900)}}])
        self.assertEqual(failed_only["multi"]["completed_cases"], 0)
        self.assertEqual(failed_only["multi"]["exact_root_set_successes"], 0)
        self.assertIsNone(failed_only["multi"]["top1_rate"])
        self.assertIsNone(failed_only["multi"]["p95_latency_ms"])

    def test_summary_reports_cost_per_exact_success_and_p95(self) -> None:
        first = {"case_id": "a", "strategies": {"rules": {"duration_ms": 100, "model_calls": 0,
            "provider_rate_estimate_usd": 0, "failures": [], "metrics": {"exact_root_set": True, "top1": True,
                "top3_recall": 1, "false_positives": 0, "evidence_true_positive": 1, "evidence_predicted": 1, "evidence_gold": 1}}}}
        second = {"case_id": "b", "strategies": {"rules": {"duration_ms": 300, "model_calls": 0,
            "provider_rate_estimate_usd": 0, "failures": ["RuntimeError"], "metrics": {"exact_root_set": False, "top1": False,
                "top3_recall": 0, "false_positives": 1, "evidence_true_positive": 0, "evidence_predicted": 0, "evidence_gold": 1}}}}
        summary = summarize_cases([first, second])
        self.assertEqual(summary["rules"]["exact_root_set_successes"], 1)
        self.assertEqual(summary["rules"]["p95_latency_ms"], 100)
        self.assertEqual(summary["rules"]["completed_cases"], 1)
        self.assertEqual(summary["rules"]["failed_cases"], 1)
        self.assertEqual(summary["rules"]["failures"][0]["case_id"], "b")


if __name__ == "__main__":
    unittest.main()
