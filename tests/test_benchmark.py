import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from opspilot.cli import load_incidents
from opspilot.memory import retrieve_memories
from scripts.generate_benchmark import generate


class BenchmarkTests(unittest.TestCase):
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
