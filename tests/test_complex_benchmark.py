from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_complex_benchmark import generate
from opspilot.cli import load_incidents


class ComplexBenchmarkTests(unittest.TestCase):
    def test_generation_is_frozen_separate_and_labels_are_external(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "complex"
            manifest = generate(output)
            second = generate(Path(temp) / "second")
            self.assertEqual(manifest, second)
            self.assertEqual({"train": 32, "validation": 16, "test": 16},
                             {name: split["cases"] for name, split in manifest["splits"].items()})
            for split, meta in manifest["splits"].items():
                data = (output / f"{split}.jsonl").read_bytes()
                labels = (output / "labels" / f"{split}.json").read_bytes()
                self.assertEqual(meta["data_sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(meta["labels_sha256"], hashlib.sha256(labels).hexdigest())
                self.assertNotIn(b"gold_evidence_ids", data)

    def test_gold_refs_exist_and_unanswerable_cases_have_no_gold(self):
        root = Path(__file__).resolve().parents[1]
        data_dir = root / "data" / "complex-benchmark"
        for split in ("train", "validation", "test"):
            incidents = load_incidents(data_dir / f"{split}.jsonl")
            labels = json.loads((data_dir / "labels" / f"{split}.json").read_text(encoding="utf-8"))["labels"]
            self.assertEqual(set(incidents), {label["incident_id"] for label in labels})
            for label in labels:
                available = {row["record_id"] for source in incidents[label["incident_id"]]["signals"].values() for row in source}
                self.assertTrue(set(label["gold_evidence_ids"]) <= available)
                if not label["decidable"]:
                    self.assertEqual([], label["gold_evidence_ids"])
                else:
                    traces = {row.get("attributes", {}).get("trace_id") for row in incidents[label["incident_id"]]["signals"]["traces"]}
                    self.assertTrue(set(label["causal_trace_ids"]) <= traces)


if __name__ == "__main__":
    unittest.main()
