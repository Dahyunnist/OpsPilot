from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.evaluate_lab import evaluate_case


ROOT = Path(__file__).resolve().parents[1]


def test_twelve_truth_rows_map_to_eleven_mechanisms_plus_one_intentional_compound():
    truth = json.loads((ROOT / "lab/scenarios/truth.json").read_text(encoding="utf-8"))
    app = (ROOT / "lab/service/app.py").read_text(encoding="utf-8")
    runner = (ROOT / "lab/run-scenario.ps1").read_text(encoding="utf-8-sig")
    cases = {case["fault"]: case for case in truth["cases"]}

    assert len(truth["cases"]) == 12
    assert len(cases) == 12
    assert len({case["case_id"] for case in truth["cases"]}) == 12
    assert len({cause for case in truth["cases"] for cause in case["root_causes"]}) == 11
    assert cases["double_root"]["root_causes"] == ["slow_database_query", "payment_dependency_timeout"]

    mechanisms = {
        "db_pool_exhaustion": "start_pool_pressure(order_id)",
        "dependency_timeout": "FAULT in {\"dependency_timeout\", \"double_root\"}",
        "bad_release": 'http://inventory-v2:8080',
        "slow_sql": 'FAULT in {"slow_sql", "double_root"}',
        "retry_storm": 'retries = 4 if FAULT == "retry_storm" else 1',
        "double_root": 'FAULT in {"slow_sql", "double_root"}',
        "inventory_latency": "time.sleep(0.8)",
        "payment_reject": 'raise ResponseError(503, "synthetic payment rejection")',
        "stale_inventory": '"replica_stale": True',
        "duplicate_delivery": '"delivery_replay": True',
        "redis_unavailable": 'redis.ConnectionError("synthetic redis unavailable")',
        "inventory_depleted": '"remaining": 0',
    }
    for fault, marker in mechanisms.items():
        source = runner if fault == "bad_release" else app
        assert fault in cases
        assert marker in source


def test_lab_services_and_agent_checkout_do_not_receive_truth_case_id_or_gold():
    compose = (ROOT / "lab/compose.yaml").read_text(encoding="utf-8")
    evaluator = (ROOT / "lab/evaluate-all.ps1").read_text(encoding="utf-8-sig")
    app = (ROOT / "lab/service/app.py").read_text(encoding="utf-8")

    assert "truth.json" not in compose and "scenarios/" not in compose
    assert "$orderId = [guid]::NewGuid().ToString(\"N\")" in evaluator
    assert "@{ order_id = $orderId }" in evaluator
    assert "@{ order_id = $case.case_id }" not in evaluator
    assert "--case-id $case.case_id" in evaluator  # local scorer selector, after the opaque checkout ID
    assert "root_causes" not in app and "must_observe" not in app


def test_live_truth_is_given_to_scorer_after_investigation_not_as_source_input(tmp_path):
    events = []

    class FakeTelemetry:
        @staticmethod
        def _result(source):
            return {"source": source, "records": [], "warnings": []}

        def metrics(self, *_args):
            return self._result("metrics")

        def logs(self, *_args):
            return self._result("logs")

        def traces(self, *_args):
            return self._result("traces")

        def deployments(self, *_args):
            return self._result("deployments")

    def fake_investigation(case_id, start, end, source_results, *args, **kwargs):
        events.append(("investigate", case_id, json.dumps(source_results, sort_keys=True)))
        return {"case_id": case_id, "strategies": {"rules": {
            "prediction": {"root_causes": []}, "provider_rate_estimate_usd": 0.0}}}

    def fake_score(report, roots, gold_refs):
        events.append(("score", list(roots), sorted(gold_refs)))
        return {**report, "truth": {"root_causes": roots}}

    with patch("scripts.evaluate_lab.LabTelemetry", FakeTelemetry), \
            patch("scripts.evaluate_lab.evaluate_snapshot", side_effect=fake_investigation), \
            patch("scripts.evaluate_lab.score_case", side_effect=fake_score):
        result = evaluate_case("lab-release-01", "2026-11-15T00:00:00Z", "2026-11-15T00:01:00Z",
            ("rules",), 0.5, tmp_path / "ledger.jsonl", 512)

    assert events[0][0] == "investigate"
    assert events[1][0] == "score"
    assert "invalid_release_configuration" not in events[0][2]
    assert result["truth"]["root_causes"] == ["invalid_release_configuration"]
