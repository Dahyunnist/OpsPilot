import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from opspilot.cli import load_incidents
from opspilot.memory import retrieve_memories
from opspilot.investigator import investigate
from scripts.generate_benchmark import generate
from scripts.evaluate_agents import BudgetLimitExceeded, RunBudget, normalize_cause, summarize


class BenchmarkTests(unittest.TestCase):
    def test_paraphrased_model_causes_normalize_without_rewriting_prediction(self):
        self.assertEqual("config_error", normalize_cause("Config bundle deployment introduced invalid configuration"))
        self.assertEqual("downstream_timeout", normalize_cause("Downstream call exceeded deadline"))
        self.assertIsNone(normalize_cause("cannot distinguish current cause"))

    def test_summary_counts_abstention_evidence_and_gate_override(self):
        cases = [
            {"truth": {"decidable": True, "category": "pool_exhaustion", "gold_evidence_ids": ["m1", "t1"]},
             "prediction": {"root_cause": "pool_exhaustion", "evidence_ids": ["m1", "t1"], "citation_validation": {"all_references_support_hypothesis": True}, "action_suggestion": "restart", "approval_required": True},
             "available_evidence_ids": ["m1", "t1"], "success": True, "duration_ms": 100,
             "trace": {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 10},
             "model": {"name": "deepseek-v4-flash", "calls": 1, "requested_approval": False}},
            {"truth": {"decidable": False, "category": "insufficient_evidence", "gold_evidence_ids": []},
             "prediction": {"root_cause": None, "evidence_ids": [], "citation_validation": {}, "action_suggestion": None, "approval_required": False},
             "available_evidence_ids": [], "success": True, "duration_ms": 80,
             "trace": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}, "model": {"calls": 0}},
        ]
        summary = summarize(cases, {"input_cache_hit_per_million": 0.006, "input_cache_miss_per_million": 0.3, "output_per_million": 1.2})
        self.assertEqual((1, 1), (summary["single_root_accuracy"]["correct"], summary["insufficient_evidence_recall"]["correct_abstentions"]))
        self.assertEqual(1, summary["citation_existence_cases"]["valid"])
        self.assertEqual(1, summary["action_safety"]["model_underapproved_suggestions"])
        self.assertEqual(0, summary["action_safety"]["ungated_suggestions"])

    def test_zero_model_calls_have_zero_cost(self):
        summary = summarize([{"truth": {"decidable": False, "category": "insufficient_evidence", "gold_evidence_ids": []},
                              "prediction": {"root_cause": None, "evidence_ids": []}, "success": True,
                              "duration_ms": 1, "trace": {}, "model": {"calls": 0}}])
        self.assertEqual(0.0, summary["estimated_cost_usd"])

    def test_summary_scores_top3_and_multiple_active_causes(self):
        case = {"truth": {"decidable": True, "category": "config_error", "primary_causes": ["config_error"],
                           "active_causes": ["config_error", "pool_exhaustion"], "gold_evidence_ids": ["c1", "p1"]},
                "prediction": {"root_cause": "config_error", "ranked_causes": ["config_error", "pool_exhaustion"],
                               "evidence_ids": ["c1", "p1"], "citation_validation": {"all_references_support_hypothesis": True}},
                "available_evidence_ids": ["c1", "p1"], "success": True, "duration_ms": 1, "trace": {}, "model": {"calls": 0}}
        summary = summarize([case])
        self.assertEqual(1.0, summary["active_cause_hit_at_3"]["rate"])
        self.assertEqual(1.0, summary["active_cause_recall_at_3"]["rate"])
        self.assertEqual(1.0, summary["active_cause_exact_set"]["rate"])
        self.assertIsNone(summary["single_root_accuracy"]["rate"])
        self.assertEqual(1.0, summary["active_cause_hit_at_1"]["rate"])

    def test_multi_root_order_does_not_change_root_hit(self):
        case = {"truth": {"decidable": True, "category": "config_error", "active_causes": ["config_error", "pool_exhaustion"], "gold_evidence_ids": []},
                "prediction": {"root_cause": "pool_exhaustion", "ranked_causes": ["pool_exhaustion", "config_error"], "evidence_ids": []},
                "available_evidence_ids": [], "success": True, "duration_ms": 1, "trace": {}, "model": {"calls": 0}}
        summary = summarize([case])
        self.assertEqual(0, summary["single_root_accuracy"]["total_single_root"])
        self.assertEqual(1.0, summary["active_cause_hit_at_1"]["rate"])
        self.assertEqual(1.0, summary["active_cause_exact_set"]["rate"])

    def test_run_budget_logs_peak_cost_and_blocks_before_overrun(self):
        with TemporaryDirectory() as temp:
            ledger = Path(temp) / "cost.jsonl"
            budget = RunBudget(0.01, ledger, "deepseek-v4-flash")
            instance = SimpleNamespace(max_tokens=512)
            result = budget.call(lambda *_args: {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
                                 instance, "system", "user", 512)
            self.assertEqual(1000, result["usage"]["prompt_tokens"])
            self.assertGreater(budget.spent_usd, 0)
            self.assertIn("completed_request", ledger.read_text(encoding="utf-8"))

            blocked = RunBudget(0.00001, ledger, "deepseek-v4-flash")
            called = False
            def operation(*_args):
                nonlocal called
                called = True
                return {"usage": {}}
            with self.assertRaises(BudgetLimitExceeded):
                blocked.call(operation, instance, "long system " * 100, "long user " * 100, 512)
            self.assertFalse(called)

    def test_frozen_stratified_splits_and_regeneration(self):
        root = Path("data/benchmark")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(48, manifest["cases"])
        all_ids = set()
        for split, expected_count in (("train", 24), ("validation", 8), ("test", 16)):
            data = (root / f"{split}.jsonl").read_bytes()
            labels = json.loads((root / "labels" / f"{split}.json").read_text(encoding="utf-8"))["labels"]
            incidents = load_incidents(root / f"{split}.jsonl")
            entry = manifest["splits"][split]
            self.assertEqual(expected_count, len(incidents))
            self.assertEqual(entry["data_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(entry["labels_sha256"], hashlib.sha256((root / "labels" / f"{split}.json").read_bytes()).hexdigest())
            self.assertFalse(all_ids & set(incidents))
            all_ids.update(incidents)
            self.assertNotIn("root_cause", next(iter(incidents.values())))
            self.assertEqual(set(incidents), {label["incident_id"] for label in labels})
        with TemporaryDirectory() as temp:
            regenerated = generate(Path(temp))
        self.assertEqual(manifest, regenerated)

    def test_memory_reads_training_labels_only_and_marks_them_advisory(self):
        incident = next(iter(load_incidents(Path("data/benchmark/test.jsonl")).values()))
        memories = retrieve_memories(incident, top_k=3)
        self.assertTrue(memories)
        self.assertLessEqual(len(memories), 3)
        self.assertTrue(all(item["memory_id"].startswith(tuple(f"{category}-" for category in ("pool-exhaustion", "config-error", "downstream-timeout", "unknown"))) for item in memories))
        self.assertTrue(all("not proof" in item["lesson"] for item in memories))

    def test_memory_excludes_the_current_training_case(self):
        incident = next(iter(load_incidents(Path("data/benchmark/train.jsonl")).values()))
        memories = retrieve_memories(incident, exclude_ids={incident["incident_id"]})
        self.assertNotIn(incident["incident_id"], {item["memory_id"] for item in memories})

    def test_weak_warning_cannot_break_a_live_signal_tie(self):
        incident = load_incidents(Path("data/benchmark/validation.jsonl"))["unknown-02"]
        report = investigate(incident)
        self.assertEqual("insufficient_evidence", report["status"])


if __name__ == "__main__":
    unittest.main()
