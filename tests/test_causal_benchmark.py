from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from opspilot.cli import load_incidents
from scripts.generate_causal_benchmark import FAMILIES, SPLITS, generate


class CausalBenchmarkTests(unittest.TestCase):
    def test_generation_is_reproducible_frozen_and_stratified(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            manifest = generate(first)
            self.assertEqual(manifest, generate(second))
            self.assertEqual(100, manifest["cases"])
            self.assertEqual({"train": 50, "validation": 20, "test": 30},
                             {name: item["cases"] for name, item in manifest["splits"].items()})
            for split, meta in manifest["splits"].items():
                data = (first / f"{split}.jsonl").read_bytes()
                label_bytes = (first / "labels" / f"{split}.json").read_bytes()
                self.assertEqual(meta["data_sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(meta["labels_sha256"], hashlib.sha256(label_bytes).hexdigest())
                self.assertEqual(data, (second / f"{split}.jsonl").read_bytes())
                self.assertEqual(label_bytes, (second / "labels" / f"{split}.json").read_bytes())

    def test_neutral_ids_labels_isolated_and_references_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = generate(root)
            ids: set[str] = set()
            for split in SPLITS:
                data_bytes = (root / f"{split}.jsonl").read_bytes()
                self.assertNotIn(b"root_cause", data_bytes)
                self.assertNotIn(b"scenario_family", data_bytes)
                incidents = load_incidents(root / f"{split}.jsonl")
                labels = json.loads((root / "labels" / f"{split}.json").read_text(encoding="utf-8"))["labels"]
                self.assertFalse(ids & set(incidents))
                ids.update(incidents)
                self.assertEqual(set(incidents), {item["incident_id"] for item in labels})
                for label in labels:
                    incident = incidents[label["incident_id"]]
                    self.assertRegex(label["incident_id"], re.compile(r"^opx-[a-z0-9]{10}$"))
                    available = {row["record_id"] for source in incident["signals"].values() for row in source}
                    self.assertTrue(set(label["gold_evidence_ids"]) <= available)
                    self.assertTrue(set(label["decoy_evidence_ids"]) <= available)
                    for cause_ids in label["cause_evidence_ids"].values():
                        self.assertTrue(set(cause_ids) <= available)
                    for edge in label["causal_edges"]:
                        self.assertIn(edge["from"], available)
                        self.assertIn(edge["to"], available)
                    if not label["decidable"]:
                        self.assertFalse(label["primary_causes"])
                        self.assertFalse(label["gold_evidence_ids"])

    def test_dual_faults_are_independent_and_unknown_sources_are_declared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generate(root)
            seen_families = set()
            for split in SPLITS:
                incidents = load_incidents(root / f"{split}.jsonl")
                labels = json.loads((root / "labels" / f"{split}.json").read_text(encoding="utf-8"))["labels"]
                for label in labels:
                    seen_families.add(label["scenario_family"])
                    incident = incidents[label["incident_id"]]
                    if label["scenario_family"] == "independent_dual_fault":
                        self.assertEqual(2, len(label["active_causes"]))
                        self.assertFalse(any(edge["from"] in label["gold_evidence_ids"] and edge["to"] in label["gold_evidence_ids"]
                                             and {edge["from"], edge["to"]} <= set(label["cause_evidence_ids"]["config_error"] + label["cause_evidence_ids"]["pool_exhaustion"])
                                             and ((edge["from"] in label["cause_evidence_ids"]["config_error"] and edge["to"] in label["cause_evidence_ids"]["pool_exhaustion"])
                                                  or (edge["from"] in label["cause_evidence_ids"]["pool_exhaustion"] and edge["to"] in label["cause_evidence_ids"]["config_error"]))
                                             for edge in label["causal_edges"]))
                    if not label["decidable"]:
                        self.assertIn("missing", incident.get("source_status", {}).values())
            self.assertEqual(set(FAMILIES), seen_families)


if __name__ == "__main__":
    unittest.main()
