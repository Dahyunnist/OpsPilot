"""Build an offline hash review for the exact authorized OP-3 prompt inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_op2_frozen import (
    FIXED_WORST_CASE_USD,
    ROOT,
    _load_frozen_cases,
    _planned_requests,
    _validate_prompt_payload,
)
from opspilot.strategy_eval import STRATEGIES, evaluate_snapshot


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _request_body_sha256(system: str, user: str, budget: int) -> tuple[str, int]:
    max_tokens = min(512, max(64, budget))
    body = {
        "model": "deepseek-v4-flash",
        "temperature": 0,
        "max_tokens": max_tokens,
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    encoded = json.dumps(body).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), max_tokens


def prepare_payload_review() -> dict[str, Any]:
    frozen, cases = _load_frozen_cases()
    captured: list[dict[str, Any]] = []
    model = SimpleNamespace(max_tokens=512)
    for case in cases:
        records_by_id = {
            row["record_id"]: row
            for result in case["sources"].values()
            for row in result.get("records", [])
        }

        def capture(_operation: Any, _instance: Any, system: str, user: str,
                    budget: int) -> dict[str, Any]:
            _validate_prompt_payload(system, user, records_by_id)
            payload = json.loads(user)
            if "live_evidence_by_source" in payload:
                kind = "single"
                source = None
                data = {"root_causes": [], "evidence_by_root": {}, "uncertainty": ""}
            elif "records" in payload and "source" in payload:
                kind = "expert"
                source = payload["source"]
                data = {"hypotheses": [], "missing_data": []}
            else:
                kind = "adjudicator"
                source = None
                data = {"root_causes": [], "evidence_by_root": {}, "uncertainty": ""}
            body_sha, max_tokens = _request_body_sha256(system, user, budget)
            captured.append({
                "case_id": case["spec"]["id"],
                "kind": kind,
                "source": source,
                "system_sha256": _sha256(system),
                "user_payload_sha256": _sha256(user),
                "request_body_sha256": body_sha,
                "token_budget": budget,
                "max_tokens": max_tokens,
                "system_prompt": system,
            })
            return {"data": data, "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "prompt_tokens_details": {"cached_tokens": 0},
            }}

        result = evaluate_snapshot(
            case["spec"]["id"], case["start"], case["end"], case["sources"],
            model, capture, STRATEGIES, token_budget=2048, retry_truncated=False,
        )
        if any(item["evaluation_status"] != "complete"
                for item in result["strategies"].values()):
            raise ValueError("offline prompt construction did not complete for every strategy")

    unique: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for item in captured:
        key = (item["case_id"], item["system_sha256"],
            item["user_payload_sha256"], item["token_budget"])
        unique[key] = item
    entries = sorted(unique.values(), key=lambda item: (
        item["case_id"], item["kind"], item["source"] or "", item["user_payload_sha256"]))
    if len(entries) != 18 or sum(item["kind"] != "adjudicator" for item in entries) != 15:
        raise ValueError("offline prompt count differs from the authorized request plan")

    system_prompts: dict[str, Any] = {"single": None, "experts": {}, "adjudicator": None}
    for item in entries:
        if item["kind"] == "single":
            system_prompts["single"] = item["system_prompt"]
        elif item["kind"] == "expert":
            system_prompts["experts"][item["source"]] = item["system_prompt"]
        else:
            system_prompts["adjudicator"] = item["system_prompt"]
    static_entries = [item for item in entries if item["kind"] != "adjudicator"]
    dynamic_entries = [{key: item[key] for key in (
        "case_id", "kind", "system_sha256", "token_budget", "max_tokens")}
        for item in entries if item["kind"] == "adjudicator"]
    for item in entries:
        item.pop("system_prompt")

    return {
        "schema_version": 1,
        "artifact_type": "offline_model_payload_hash_review",
        "status": "verified_frozen_inputs_and_prompt_contract",
        "model": "deepseek-v4-flash",
        "temperature": 0,
        "route_token_budget": 2048,
        "max_completion_tokens": 512,
        "spec_sha256": frozen["spec_sha256"],
        "cases": [{"case_id": case["spec"]["id"],
            "snapshot": case["spec"]["snapshot"],
            "snapshot_sha256": case["snapshot_sha256"]} for case in cases],
        "strategies": list(STRATEGIES),
        "request_plan": _planned_requests(cases, STRATEGIES),
        "request_plan_max_provider_requests": 18,
        "worst_case_reserve_usd": FIXED_WORST_CASE_USD,
        "static_request_count": 15,
        "static_request_hashes": static_entries,
        "dynamic_adjudicator_count": 3,
        "dynamic_adjudicator_templates": dynamic_entries,
        "dynamic_adjudicator_contract": {
            "user_payload_sha256": "response-dependent; recorded in run audit before each adjudicator request",
            "user_payload_fields": ["experts", "all_evidence_ids"],
            "evidence_ids": "exact IDs from the corresponding frozen snapshot",
            "expert_outputs": "source-isolated outputs sanitized by evaluator; cited IDs validated against the frozen snapshot",
            "gold_or_private_data": "not included; evaluator rejects gold keys and records outside the frozen snapshot",
        },
        "system_prompts": system_prompts,
        "validation": {
            "model_calls": 0,
            "provider_requests": 0,
            "gold_in_payload": False,
            "credentials_in_payload": False,
            "real_customer_data_in_payload": False,
            "checkpoint_or_ledger_written": False,
        },
    }


if __name__ == "__main__":
    report = prepare_payload_review()
    target = ROOT / "reports" / "op3-multiagent-payload-review-2026-11-15.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": target.relative_to(ROOT).as_posix(),
        "status": report["status"], "request_count": len(report["static_request_hashes"]),
        "dynamic_adjudicators": report["dynamic_adjudicator_count"],
        "provider_requests": 0}, ensure_ascii=False, indent=2))
