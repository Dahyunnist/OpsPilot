from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import evaluate_lab


class IncompleteTelemetry:
    def __getattr__(self, _source: str):
        return lambda *_args: {"records": []}


class EvaluateLabGuardTests(unittest.TestCase):
    def test_incomplete_live_baseline_blocks_model_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(evaluate_lab, "LabTelemetry", IncompleteTelemetry), \
                 patch.object(evaluate_lab, "ChatModel", side_effect=AssertionError("model must not start")):
                with self.assertRaisesRegex(RuntimeError, "live Docker baseline incomplete; model requests skipped"):
                    evaluate_lab.evaluate_case("lab-pool-01", "2026-10-23T10:00:00Z",
                        "2026-10-23T10:10:00Z", ("single",), 0.5,
                        Path(temporary) / "ledger.jsonl", 2048)


if __name__ == "__main__":
    unittest.main()
