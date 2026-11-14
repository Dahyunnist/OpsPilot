from __future__ import annotations

import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opspilot.strategy_eval import CAUSES, STRATEGIES
from opspilot.run_manifest import validate_op_run_artifact
from scripts.evaluate_op2_frozen import (STRATEGIES as RUNNER_STRATEGIES, _load_frozen_cases,
    _load_request_checkpoints, _prior_completed_requests, _prior_subset_spend, _total_cap_covers,
    _validate_prompt_payload, AUTHORIZATION_SCOPE, FROZEN_LIVE_CASES, main, run)
from scripts.evaluate_agents import BudgetLimitExceeded, RunBudget


class FrozenOp3EvaluationTests(unittest.TestCase):
    def test_run_reports_three_case_authorization_scope_consistently(self):
        import inspect
        from scripts.evaluate_op2_frozen import run as evaluation_run

        self.assertEqual("three_case_frozen_subset", AUTHORIZATION_SCOPE)
        self.assertEqual(2, inspect.getsource(evaluation_run).count("AUTHORIZATION_SCOPE"))

    def test_total_cap_check_accepts_float_ledger_and_decimal_reserve(self):
        from decimal import Decimal

        self.assertTrue(_total_cap_covers(0.49932072, Decimal("0.0230226"), Decimal("0.60")))
        self.assertFalse(_total_cap_covers(0.49932072, Decimal("0.12"), Decimal("0.60")))

    def test_per_run_reserve_blocks_before_call_even_when_project_cap_has_room(self):
        with TemporaryDirectory() as temp:
            ledger = Path(temp) / "cost-ledger.jsonl"
            ledger.write_text(json.dumps({"kind": "run_summary", "run_id": "prior",
                "peak_rate_estimate_usd": 0.49932072}) + "\n", encoding="utf-8")
            budget = RunBudget(0.60, ledger, "synthetic-model", run_limit_usd=0.0230226)
            called = []
            with self.assertRaises(BudgetLimitExceeded):
                budget.call(lambda *_: called.append(True), SimpleNamespace(max_tokens=512), "s" * 80_000, "u", 512)
            self.assertEqual([], called)
            self.assertLess(budget.prior_spent_usd, budget.limit_usd)
            self.assertEqual(0.0, budget.spent_usd)

    def test_preregistered_snapshots_and_shared_strategy_order_are_frozen(self):
        spec, cases = _load_frozen_cases()
        self.assertEqual(["rules", "single", "multi", "auto"], list(STRATEGIES))
        self.assertEqual(list(STRATEGIES), list(RUNNER_STRATEGIES))
        self.assertEqual(3, len(cases))
        self.assertEqual(3, len({case["snapshot_sha256"] for case in cases}))
        self.assertTrue(all(case["start"] < case["end"] for case in cases))
        self.assertEqual(spec["total_worst_case_usd"], 0.0230226)
        self.assertEqual(spec["calls_made"], 0)

    def test_live_selection_loads_all_manifest_hash_verified_cases(self):
        _spec, cases = _load_frozen_cases(set(FROZEN_LIVE_CASES))
        self.assertEqual(["normal_slow_sql", "missing_pool_evidence", "contradictory_payment"],
            [case["spec"]["id"] for case in cases])
        self.assertEqual(FROZEN_LIVE_CASES,
            {case["spec"]["id"]: case["snapshot_sha256"] for case in cases})

    def test_frozen_preflight_rejects_changed_registry_and_snapshot_path(self):
        import copy
        from scripts import evaluate_op2_frozen

        with patch("scripts.evaluate_op2_frozen.ALLOWED_SNAPSHOT_HASHES", {"unexpected.json": "a" * 64}):
            with self.assertRaisesRegex(ValueError, "allowlist differs"):
                _load_frozen_cases()

        spec, _cases = _load_frozen_cases()
        with TemporaryDirectory() as temp:
            altered = copy.deepcopy(spec)
            altered["cases"][0]["snapshot"] = "reports/../" + Path(altered["cases"][0]["snapshot"]).name
            path = Path(temp) / "subset.json"
            path.write_text(json.dumps(altered), encoding="utf-8")
            with patch("scripts.evaluate_op2_frozen.SUBSET_PATH", path):
                with self.assertRaisesRegex(ValueError, "outside allowlist"):
                    _load_frozen_cases()

    def test_live_run_requires_both_explicitly_authorized_case_ids(self):
        with TemporaryDirectory() as temp:
            with patch("scripts.evaluate_op2_frozen.load_project_env") as load_env:
                with self.assertRaisesRegex(ValueError, "every case in the hash-verified frozen manifest"):
                    run(Path(temp) / "new-report.json")
                load_env.assert_not_called()

    def test_prompt_allowlist_accepts_only_current_snapshot_records_without_gold(self):
        _spec, cases = _load_frozen_cases()
        records = {row["record_id"]: row for result in cases[0]["sources"].values()
            for row in result["records"]}
        row = next(iter(records.values()))
        source = row["source"]
        payload = {"allowed_causes": [{"id": cause, "title": title} for cause, title in CAUSES],
            "live_evidence_by_source": {name: [item for item in records.values() if item["source"] == name]
                for name in ("metrics", "logs", "traces", "deployments")}}
        _validate_prompt_payload("fixed policy prompt", json.dumps(payload), records)
        with self.assertRaisesRegex(ValueError, "labels are forbidden"):
            _validate_prompt_payload("fixed policy prompt", json.dumps({**payload, "truth": []}), records)
        bad_payload = {"source": source, "allowed_causes": sorted(cause for cause, _ in CAUSES),
            "records": [{**row, "record_id": "not-in-frozen-snapshot"}]}
        with self.assertRaisesRegex(ValueError, "outside the frozen snapshot"):
            _validate_prompt_payload("fixed policy prompt", json.dumps(bad_payload), records)

    def test_real_run_requires_explicit_execute_switch(self):
        with patch("sys.argv", ["evaluate_op2_frozen.py"]), self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(2, raised.exception.code)

    def test_fixed_report_remains_partial_and_does_not_claim_strategy_gain(self):
        from pathlib import Path
        report = json.loads((Path(__file__).resolve().parents[1] / "reports" /
            "op3-op2-subset-evaluation-fixed-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual("partial", report["evaluation_status"])
        self.assertEqual(0.0, report["budget"]["actual_run_peak_usd"])
        self.assertEqual({"rules": 3, "single": 2, "multi": 0, "auto": 2},
            {name: values["completed_cases"] for name, values in report["strategy_summary"].items()})
        self.assertTrue(any("PriorPaidResponseUnavailable" in reason
            for case in report["per_case"] for strategy in case["strategies"].values()
            for reason in strategy["failures"]))
        self.assertTrue(any("BudgetLimitExceeded" in reason
            for case in report["per_case"] for strategy in case["strategies"].values()
            for reason in strategy["failures"]))
        self.assertEqual(0, report["strategy_summary"]["multi"]["model_calls"])

    def test_frozen_response_audit_reuses_only_approved_hashes_at_zero_cost(self):
        root = Path(__file__).resolve().parents[1]
        audit = json.loads((root / "reports" /
            "op3-op2-response-audit-2026-11-14.json").read_text(encoding="utf-8"))
        _spec, cases = _load_frozen_cases()
        self.assertEqual(0, audit["new_model_calls"])
        self.assertEqual(0.0, audit["incremental_cost_usd"])
        self.assertEqual(6, audit["raw_provider_responses_available_in_checkpoint"])
        self.assertTrue(all(item["snapshot_matches_manifest"] for item in audit["cases"]))
        self.assertEqual([True, True, False], [item["authorization_hash_match"]
            for item in audit["cases"]])
        self.assertEqual("de76add2b1dc15e4da2207632d44c39c39ba813",
            audit["cases"][2]["user_authorization_reference"])
        self.assertEqual("de76add2b1dc15e4da2207632d44e5cfa4c849abe9a82604a34cfc49423ba813",
            audit["cases"][2]["local_snapshot_sha256"])
        self.assertEqual({case["spec"]["id"] for case in cases},
            {item["case_id"] for item in audit["cases"]})

    def test_live_evaluation_preflight_rejection_records_zero_egress_and_cost(self):
        from pathlib import Path
        audit = json.loads((Path(__file__).resolve().parents[1] / "reports" /
            "op3-op2-authorized-two-preflight-blocked-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual("blocked_before_request_egress", audit["status"])
        self.assertEqual(0, audit["execution"]["new_model_calls"])
        self.assertEqual(0.0, audit["execution"]["incremental_cost_usd"])
        self.assertFalse(audit["execution"]["request_command_process_started"])
        self.assertFalse(audit["provider_read_only_preflight"]["exact_configured_model_id_listed"])

    def test_offline_preflight_audit_binds_frozen_manifests_and_preserves_partial_result(self):
        root = Path(__file__).resolve().parents[1]
        audit = json.loads((root / "reports" /
            "op2-op3-offline-preflight-audit-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual([], validate_op_run_artifact(audit, is_new_run=True))
        self.assertEqual(3, len(audit["frozen_cases"]))
        self.assertTrue(all(item["snapshot_hash_match"] and item["snapshot_manifest_valid"]
            for item in audit["frozen_cases"]))
        self.assertTrue(audit["op2_readonly_regressions"]["passed"])
        self.assertEqual("partial", audit["op3_existing_evaluation"]["status"])
        self.assertEqual("blocked_before_request_egress", audit["op3_existing_preflight_failure"]["status"])
        self.assertEqual(0, audit["op3_existing_preflight_failure"]["new_model_calls"])
        self.assertEqual(0.0, audit["op3_existing_preflight_failure"]["incremental_cost_usd"])

    def test_prior_successful_payloads_are_loaded_by_stable_request_key(self):
        with TemporaryDirectory() as temp:
            report_path = Path(temp) / "prior.json"
            report_path.write_text(json.dumps({"request_payload_audit": [{
                "case_id": "synthetic_case", "system_sha256": "a" * 64,
                "user_payload_sha256": "b" * 64, "token_budget": 320, "status": "completed"}, {
                "case_id": "synthetic_case", "system_sha256": "c" * 64,
                "user_payload_sha256": "d" * 64, "token_budget": 320, "status": "failed_or_blocked"}]}), encoding="utf-8")

            completed, audit = _prior_completed_requests([report_path])

        self.assertEqual({("synthetic_case", "a" * 64, "b" * 64, 320)}, completed)
        self.assertEqual(2, len(audit))
        self.assertTrue(all(item["prior_report"] == "prior.json" for item in audit))

    def test_request_checkpoint_reuses_completed_response_and_fails_closed_on_pending(self):
        with TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "requests.jsonl"
            base = {"case_id": "synthetic_case", "system_sha256": "a" * 64,
                "user_payload_sha256": "b" * 64, "token_budget": 320}
            checkpoint.write_text("\n".join(json.dumps(item) for item in [
                {**base, "status": "pending"},
                {**base, "status": "completed", "response": {"data": {"root_causes": []}, "usage": {}}},
                {**base, "status": "pending"},
            ]) + "\n", encoding="utf-8")

            responses, uncertain = _load_request_checkpoints(checkpoint)

        key = ("synthetic_case", "a" * 64, "b" * 64, 320)
        self.assertNotIn(key, responses)
        self.assertIn(key, uncertain)

    def test_completed_checkpoint_can_be_reused_without_new_request(self):
        with TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "requests.jsonl"
            response = {"data": {"root_causes": ["example"]}, "usage": {"prompt_tokens": 1}}
            checkpoint.write_text(json.dumps({"case_id": "synthetic_case", "system_sha256": "a" * 64,
                "user_payload_sha256": "b" * 64, "token_budget": 320,
                "status": "completed", "response": response}) + "\n", encoding="utf-8")

            responses, uncertain = _load_request_checkpoints(checkpoint)

        key = ("synthetic_case", "a" * 64, "b" * 64, 320)
        self.assertEqual(response, responses[key])
        self.assertNotIn(key, uncertain)

    def test_completed_checkpoint_in_later_source_overrides_an_older_pending_entry(self):
        with TemporaryDirectory() as temp:
            older = Path(temp) / "older.jsonl"
            newer = Path(temp) / "newer.jsonl"
            base = {"case_id": "synthetic_case", "system_sha256": "a" * 64,
                "user_payload_sha256": "b" * 64, "token_budget": 320}
            older.write_text(json.dumps({**base, "status": "pending"}) + "\n", encoding="utf-8")
            response = {"data": {"root_causes": ["example"]}, "usage": {"prompt_tokens": 1}}
            newer.write_text(json.dumps({**base, "status": "completed", "response": response}) + "\n",
                encoding="utf-8")

            responses, uncertain = _load_request_checkpoints([older, newer])

        key = ("synthetic_case", "a" * 64, "b" * 64, 320)
        self.assertEqual(response, responses[key])
        self.assertNotIn(key, uncertain)

    def test_subset_spend_includes_checkpointed_run_missing_summary(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            ledger = root / "api-cost-ledger.jsonl"
            checkpoint = root / "op3-op2-subset-evaluation-resume.requests.jsonl"
            ledger.write_text(json.dumps({"kind": "completed_request", "run_id": "interrupted",
                "run_peak_estimate_usd": 0.004}) + "\n", encoding="utf-8")
            checkpoint.write_text(json.dumps({"run_id": "interrupted", "status": "completed"}) + "\n",
                encoding="utf-8")

            spend = _prior_subset_spend(ledger)

        self.assertEqual(0.004, float(spend))

    def test_existing_report_is_never_overwritten_before_preflight(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "existing.json"
            output.write_text("keep", encoding="utf-8")
            with patch("scripts.evaluate_op2_frozen.load_project_env") as load_env:
                with self.assertRaises(FileExistsError):
                    run(output)
                load_env.assert_not_called()

    def test_latest_three_case_preflight_blocked_before_inference(self):
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root / "reports" /
            "op3-three-case-preflight-blocked-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual(3, report["frozen_dataset"]["case_count"])
        self.assertTrue(all(case["hash_match"] for case in report["frozen_dataset"]["cases"]))
        self.assertTrue(report["budget"]["within_total_cap"])
        self.assertEqual(0, report["execution"]["model_calls"])
        self.assertFalse(report["execution"]["inference_requests_started"])
        self.assertEqual("blocked_before_inference", report["evaluation_status"])

    def test_three_case_run_failure_audit_preserves_partial_counts(self):
        root = Path(__file__).resolve().parents[1]
        audit = json.loads((root / "reports" /
            "op3-three-case-run-failure-audit-2026-11-14.json").read_text(encoding="utf-8"))
        self.assertEqual("partial", audit["original_evaluation_status"])
        self.assertEqual({"rules": 3, "single": 2, "multi": 0, "auto": 2}, audit["completed_cases"])
        self.assertEqual(2, audit["multi_value_error_cases"])
        self.assertEqual("blocked_by_per_run_reserve", audit["budget_stop_classification"])
        self.assertFalse(audit["original_report_modified"])


if __name__ == "__main__":
    unittest.main()
