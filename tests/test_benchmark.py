import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from opspilot.cli import load_incidents
from opspilot.memory import retrieve_memories
from scripts.generate_benchmark import generate
from scripts.evaluate_agents import normalize_cause, summarize


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
        self.assertEqual((1, 1), (summary["root_cause_accuracy"]["correct"], summary["insufficient_evidence_recall"]["correct_abstentions"]))
        self.assertEqual(1, summary["citation_existence_cases"]["valid"])
        self.assertEqual(1, summary["action_safety"]["model_underapproved_suggestions"])
        self.assertEqual(0, summary["action_safety"]["ungated_suggestions"])

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


if __name__ == "__main__":
    unittest.main()
