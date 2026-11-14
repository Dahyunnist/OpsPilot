"""Run the preregistered OP-2 telemetry subset through real OP-3 strategies."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.model_client import ChatModel, load_project_env, redact  # noqa: E402
from opspilot.strategy_eval import CAUSES, STRATEGIES, evaluate_snapshot, score_case, summarize_cases  # noqa: E402
from scripts.evaluate_agents import RunBudget  # noqa: E402
from scripts.evaluate_lab import _gold_evidence  # noqa: E402

SUBSET_PATH = ROOT / "reports" / "op2-model-subset.json"
CAP_USD = 0.60
FIXED_WORST_CASE_USD = 0.0230226
FIXED_COMPLETION_LIMIT = 512
DEFAULT_OUTPUT = ROOT / "reports" / "op3-op2-subset-evaluation.json"
ALLOWED_SNAPSHOT_HASHES = {
    "op2-normal_slow_sql-snapshot.json": "d450db5fc6851dabf7d732cd07ea7e229a872c76e29cfc39a3add2466749c9d8",
    "op2-missing_pool_evidence-snapshot.json": "d244e00980dfb160831df77ae187208c186a4ba0489831d1b4bda81ef68bbbe7",
    "op2-contradictory_payment-snapshot.json": "de76add2b1dc15e4da2207632d44e5cfa4c849abe9a82604a34cfc49423ba813",
}
FROZEN_LIVE_CASES = {Path(name).stem.removeprefix("op2-").removesuffix("-snapshot"): digest
    for name, digest in ALLOWED_SNAPSHOT_HASHES.items()}


class PriorPaidResponseUnavailable(RuntimeError):
    """A prior request was paid, but its response body was not checkpointed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request_key(case_id: str, system_sha256: str, user_sha256: str, token_budget: int) -> tuple[str, str, str, int]:
    return case_id, system_sha256, user_sha256, token_budget


def _prior_reports(output: Path) -> list[Path]:
    return sorted(path for path in (ROOT / "reports").glob("op3-op2-subset-evaluation*.json")
        if path.resolve() != output.resolve())


def _prior_completed_requests(reports: list[Path]) -> tuple[set[tuple[str, str, str, int]], list[dict[str, Any]]]:
    completed: set[tuple[str, str, str, int]] = set()
    audit: list[dict[str, Any]] = []
    for path in reports:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"prior evaluation report is unreadable: {path.name}") from exc
        for item in report.get("request_payload_audit", []):
            audit.append({**item, "prior_report": path.name})
            if item.get("status") == "completed":
                completed.add(_request_key(item["case_id"], item["system_sha256"],
                    item["user_payload_sha256"], int(item["token_budget"])))
    return completed, audit


def _checkpoint_path(output: Path) -> Path:
    return output.with_suffix(".requests.jsonl")


def _load_request_checkpoints(path: Path | list[Path]) -> tuple[dict[tuple[str, str, str, int], dict[str, Any]], set[tuple[str, str, str, int]]]:
    """Load durable paid responses; pending entries are treated as possibly billed."""
    responses: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    uncertain: set[tuple[str, str, str, int]] = set()
    paths = [path] if isinstance(path, Path) else path
    for checkpoint in paths:
        if not checkpoint.exists():
            continue
        try:
            records = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("request checkpoint is unreadable; refusing paid requests") from exc
        for record in records:
            key = _request_key(record["case_id"], record["system_sha256"],
                record["user_payload_sha256"], int(record["token_budget"]))
            status = record.get("status")
            if status == "completed" and isinstance(record.get("response"), dict):
                responses[key] = record["response"]
                uncertain.discard(key)
            elif status in {"pending", "failed_or_blocked"}:
                responses.pop(key, None)
                uncertain.add(key)
    return responses, uncertain


def _append_request_checkpoint(path: Path, record: dict[str, Any], lock: threading.Lock) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _prior_subset_spend(ledger_path: Path) -> Decimal:
    if not ledger_path.exists():
        return Decimal("0")
    try:
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cost ledger is unreadable; refusing paid requests") from exc
    summaries = [row for row in records if row.get("kind") == "run_summary"
        and row.get("benchmark") == "opspilot_op2_frozen_real_agent"]
    summarized_ids = {row.get("run_id") for row in summaries if row.get("run_id")}
    checkpoint_run_ids: set[str] = set()
    for checkpoint in ledger_path.parent.glob("op3-op2-subset-evaluation*.requests.jsonl"):
        try:
            checkpoint_records = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("request checkpoint is unreadable; refusing paid requests") from exc
        checkpoint_run_ids.update(row["run_id"] for row in checkpoint_records if row.get("run_id"))
    incomplete_ids = checkpoint_run_ids - summarized_ids
    incomplete_peak: dict[str, Decimal] = {}
    for row in records:
        run_id = row.get("run_id")
        if run_id not in incomplete_ids or row.get("kind") not in {"completed_request", "failed_request_worst_case"}:
            continue
        peak = Decimal(str(row.get("run_peak_estimate_usd", 0)))
        incomplete_peak[run_id] = max(incomplete_peak.get(run_id, Decimal("0")), peak)
    return (sum((Decimal(str(row.get("peak_rate_estimate_usd", 0))) for row in summaries), Decimal("0"))
        + sum(incomplete_peak.values(), Decimal("0")))


def _json_request(url: str, api_key: str) -> dict[str, Any]:
    request = Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(256_000))
    if not isinstance(payload, dict):
        raise ValueError("provider preflight returned non-object JSON")
    return payload


def _load_frozen_cases(case_ids: set[str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec_bytes = SUBSET_PATH.read_bytes()
    spec = json.loads(spec_bytes)
    registered = {Path(item["snapshot"]).name: item["sha256"] for item in spec.get("cases", [])}
    if registered != ALLOWED_SNAPSHOT_HASHES:
        raise ValueError("frozen subset file allowlist differs")
    cases = []
    for item in spec["cases"]:
        if case_ids is not None and item["id"] not in case_ids:
            continue
        if item["snapshot"] != f"reports/{Path(item['snapshot']).name}":
            raise ValueError(f"snapshot path is outside allowlist: {item['id']}")
        path = ROOT / item["snapshot"]
        digest = _sha256(path)
        if digest != item["sha256"] or digest != ALLOWED_SNAPSHOT_HASHES[path.name]:
            raise ValueError(f"frozen snapshot hash mismatch: {item['id']}")
        sources = json.loads(path.read_text(encoding="utf-8-sig"))
        if set(sources) != {"metrics", "logs", "traces", "deployments"}:
            raise ValueError(f"unexpected source set: {item['id']}")
        starts = {result.get("query", {}).get("start") for result in sources.values()}
        ends = {result.get("query", {}).get("end") for result in sources.values()}
        if len(starts) != 1 or len(ends) != 1 or None in starts or None in ends:
            raise ValueError(f"source windows differ: {item['id']}")
        cases.append({"spec": item, "snapshot_path": path, "snapshot_sha256": digest,
            "sources": sources, "start": next(iter(starts)), "end": next(iter(ends))})
    if case_ids is not None and {case["spec"]["id"] for case in cases} != case_ids:
        raise ValueError("selected cases differ from the authorized frozen snapshot allowlist")
    return {**spec, "spec_sha256": hashlib.sha256(spec_bytes).hexdigest()}, cases


def _validate_prompt_payload(system: str, user: str, records_by_id: dict[str, dict[str, Any]]) -> None:
    payload = json.loads(user)
    forbidden = {"truth", "root_causes", "gold_evidence_ids", "removed_gold_ids", "independent_questions", "fault", "oracle"}
    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden.intersection(str(key).lower() for key in value):
                raise ValueError("evaluation labels are forbidden in model payload")
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(payload)
    expected_causes = {cause for cause, _title in CAUSES}
    if "live_evidence_by_source" in payload:
        if set(payload) != {"allowed_causes", "live_evidence_by_source"}:
            raise ValueError("single-agent prompt fields differ from allowlist")
        if {item.get("id") for item in payload["allowed_causes"]} != expected_causes:
            raise ValueError("single-agent cause vocabulary differs from allowlist")
        evidence = payload["live_evidence_by_source"]
        if not isinstance(evidence, dict) or set(evidence) - {"metrics", "logs", "traces", "deployments"}:
            raise ValueError("single-agent payload source is outside allowlist")
        rows = [(source, row) for source, source_rows in evidence.items() for row in source_rows]
    elif "records" in payload and "source" in payload:
        if set(payload) != {"source", "allowed_causes", "records"}:
            raise ValueError("specialist prompt fields differ from allowlist")
        if set(payload["allowed_causes"]) != expected_causes:
            raise ValueError("specialist cause vocabulary differs from allowlist")
        source = payload["source"]
        if source not in {"metrics", "logs", "traces", "deployments"}:
            raise ValueError("specialist payload source is outside allowlist")
        rows = [(source, row) for row in payload["records"]]
    elif "experts" in payload and "all_evidence_ids" in payload:
        if set(payload) != {"experts", "all_evidence_ids"}:
            raise ValueError("adjudicator prompt fields differ from allowlist")
        allowed_ids = set(records_by_id)
        cited = set(payload["all_evidence_ids"])
        for expert in payload["experts"]:
            if expert.get("source") not in {"metrics", "logs", "traces", "deployments"}:
                raise ValueError("adjudicator payload source is outside allowlist")
            cited.update(ref for finding in expert.get("hypotheses", []) for ref in finding.get("evidence_ids", []))
        if not cited <= allowed_ids:
            raise ValueError("adjudicator payload cites records outside frozen snapshots")
        rows = []
    else:
        raise ValueError("unrecognized strategy payload shape")
    for source, row in rows:
        record_id = row.get("record_id") if isinstance(row, dict) else None
        original = records_by_id.get(record_id)
        if not original or original.get("source") != source or redact(row) != redact(original):
            raise ValueError("model payload contains a record outside the frozen snapshot allowlist")
    if not system.strip():
        raise ValueError("empty evaluation prompt")


def run(output: Path, case_ids: list[str] | None = None) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite prior evaluation report: {output.name}")
    if case_ids is None or set(case_ids) != set(FROZEN_LIVE_CASES) or len(case_ids) != len(FROZEN_LIVE_CASES):
        raise ValueError("live execution requires every case in the hash-verified frozen manifest")
    checkpoint_path = _checkpoint_path(output)
    if checkpoint_path.exists():
        raise FileExistsError(f"refusing to overwrite prior request checkpoint: {checkpoint_path.name}")
    load_project_env(ROOT / ".env")
    configured_model = os.getenv("LLM_MODEL", "")
    base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "")
    if configured_model != "deepseek-v4-flash":
        raise ValueError("frozen OP-3 subset requires configured model deepseek-v4-flash")
    if urlsplit(base_url).scheme != "https" or urlsplit(base_url).hostname != "api.deepseek.com" or not api_key:
        raise ValueError("provider must be official DeepSeek API with configured credentials")

    frozen, cases = _load_frozen_cases(set(case_ids))
    if frozen.get("model") != configured_model or frozen.get("max_completion_tokens") != FIXED_COMPLETION_LIMIT:
        raise ValueError("frozen subset model or completion cap differs")
    if tuple(frozen.get("strategies", [])) != STRATEGIES:
        raise ValueError("frozen subset strategy order differs")

    ledger = ROOT / "reports" / "api-cost-ledger.jsonl"
    prior_eval_spend = _prior_subset_spend(ledger)
    reserved_case_cap = sum((Decimal(str(case["spec"]["worst_case_usd"])) for case in cases), Decimal("0"))
    budget = RunBudget(CAP_USD, ledger, configured_model, run_limit_usd=float(reserved_case_cap))
    if budget.prior_spent_usd + reserved_case_cap > Decimal(str(CAP_USD)):
        raise ValueError("remaining project cap cannot cover the complete selected-case peak reserve; no inference calls sent")

    prior_report_paths = _prior_reports(output)
    prior_completed_requests, prior_request_audit = _prior_completed_requests(prior_report_paths)
    selected_ids = set(case_ids)
    prior_completed_requests = {key for key in prior_completed_requests if key[0] in selected_ids}
    prior_request_audit = [item for item in prior_request_audit if item.get("case_id") in selected_ids]
    reusable_checkpoints = sorted(path for path in ledger.parent.glob("op3-op2-subset-evaluation*.requests.jsonl")
        if path.resolve() != checkpoint_path.resolve())
    checkpoint_responses, checkpoint_uncertain = _load_request_checkpoints([*reusable_checkpoints, checkpoint_path])
    checkpoint_responses = {key: value for key, value in checkpoint_responses.items() if key[0] in selected_ids}
    checkpoint_uncertain = {key for key in checkpoint_uncertain if key[0] in selected_ids}

    # Both authenticated GETs are free preflight calls. No inference request is sent here.
    balance = _json_request("https://api.deepseek.com/user/balance", api_key)
    models = _json_request("https://api.deepseek.com/models", api_key)
    if balance.get("is_available") is not True:
        raise ValueError("provider reports insufficient balance; no inference calls sent")
    if not any(float(item.get("total_balance", 0)) > 0 for item in balance.get("balance_infos", [])):
        raise ValueError("provider balance is zero; no inference calls sent")
    model_ids = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
    if not model_ids or not model_ids.intersection({"deepseek-flash", "deepseek-v4-flash"}):
        raise ValueError("provider does not list the frozen Flash model family; no inference calls sent")

    # Frozen registry caps completions at 512 regardless of the wider .env setting.
    os.environ["LLM_MAX_COMPLETION_TOKENS"] = str(FIXED_COMPLETION_LIMIT)
    model = ChatModel()
    started = datetime.now(timezone.utc).isoformat()
    request_audit: list[dict[str, Any]] = []
    audit_lock = threading.Lock()
    checkpoint_lock = threading.Lock()
    in_run_responses: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    provider_response_metadata: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    scored_cases = []
    for case in cases:
        records_by_id = {row["record_id"]: row for result in case["sources"].values()
            for row in result.get("records", [])}

        def audited_call(operation: Any, instance: Any, system: str, user: str, token_budget: int) -> dict[str, Any]:
            _validate_prompt_payload(system, user, records_by_id)
            key = _request_key(case["spec"]["id"], hashlib.sha256(system.encode("utf-8")).hexdigest(),
                hashlib.sha256(user.encode("utf-8")).hexdigest(), token_budget)
            with audit_lock:
                prior_result = in_run_responses.get(key)
                if prior_result is not None:
                    request_audit.append({"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "token_budget": token_budget, "status": "reused_in_run"})
                    return prior_result
                prior_checkpoint = checkpoint_responses.get(key)
                if prior_checkpoint is not None:
                    request_audit.append({"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "token_budget": token_budget, "status": "reused_checkpoint"})
                    _append_request_checkpoint(checkpoint_path, {"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "token_budget": token_budget, "run_id": budget.run_id,
                        "status": "completed", "reused_existing_checkpoint": True,
                        "completed_at": datetime.now(timezone.utc).isoformat(), "response": redact(prior_checkpoint)},
                        checkpoint_lock)
                    in_run_responses[key] = prior_checkpoint
                    provider_response_metadata[key] = {"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "provider_model": prior_checkpoint.get("provider_model"),
                        "provider_system_fingerprint": prior_checkpoint.get("provider_system_fingerprint"),
                        "source": "reused_existing_checkpoint"}
                    return prior_checkpoint
            audit = {"case_id": case["spec"]["id"],
                "system_sha256": key[1], "user_payload_sha256": key[2],
                "token_budget": token_budget, "status": "pending"}
            if key in prior_completed_requests or key in checkpoint_uncertain:
                audit["previous_paid_response_unavailable"] = True
            with audit_lock:
                request_audit.append(audit)

            request_record = {"case_id": key[0], "system_sha256": key[1],
                "user_payload_sha256": key[2], "token_budget": token_budget, "run_id": budget.run_id}

            def checkpointed_operation(target: Any, fixed_system: str, fixed_user: str, fixed_budget: int) -> dict[str, Any]:
                _append_request_checkpoint(checkpoint_path,
                    {**request_record, "status": "pending", "started_at": datetime.now(timezone.utc).isoformat()},
                    checkpoint_lock)
                try:
                    response = operation(target, fixed_system, fixed_user, fixed_budget)
                except Exception as exc:
                    _append_request_checkpoint(checkpoint_path,
                        {**request_record, "status": "failed_or_blocked", "failed_at": datetime.now(timezone.utc).isoformat(),
                            "error_type": type(exc).__name__},
                        checkpoint_lock)
                    raise
                _append_request_checkpoint(checkpoint_path,
                    {**request_record, "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
                        "response": redact(response)}, checkpoint_lock)
                return response

            try:
                result = budget.call(checkpointed_operation, instance, system, user, token_budget,
                    request_metadata={"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "token_budget": token_budget})
            except Exception as exc:
                audit.update({"status": "failed_or_blocked", "error_type": type(exc).__name__})
                raise
            audit["status"] = "completed"
            with audit_lock:
                in_run_responses[key] = result
                provider_response_metadata[key] = {"case_id": key[0], "system_sha256": key[1],
                    "user_payload_sha256": key[2], "provider_model": result.get("provider_model"),
                    "provider_system_fingerprint": result.get("provider_system_fingerprint"),
                    "source": "new_provider_response"}
            return result

        report = evaluate_snapshot(case["spec"]["id"], case["start"], case["end"],
            case["sources"], model, audited_call, STRATEGIES, token_budget=2048)
        truth = case["spec"]["root_causes"]
        report = score_case(report, truth, _gold_evidence(truth, case["sources"]))
        report["frozen_case"] = {"id": case["spec"]["id"], "fault": case["spec"]["fault"],
            "independent_questions": case["spec"]["independent_questions"],
            "snapshot": case["spec"]["snapshot"], "snapshot_sha256": case["snapshot_sha256"]}
        scored_cases.append(report)

    request_audit.sort(key=lambda item: (item["case_id"], item["system_sha256"],
        item["user_payload_sha256"], item["token_budget"]))
    actual = round(budget.spent_usd, 8)
    cumulative = round(budget.prior_spent_usd + actual, 8)
    stopped = any("BudgetLimitExceeded" in failure for case in scored_cases
        for item in case["strategies"].values() for failure in item["failures"])
    has_failures = any(item["failures"] for case in scored_cases
        for item in case["strategies"].values())
    budget._append({"kind": "run_summary", "run_id": budget.run_id,
        "benchmark": "opspilot_op2_frozen_real_agent", "strategies": list(STRATEGIES),
        "case_ids": [case["spec"]["id"] for case in cases], "peak_rate_estimate_usd": actual,
        "provider_rate_estimate_usd": round(sum(item["provider_rate_estimate_usd"]
            for case in scored_cases for item in case["strategies"].values()), 8),
        "cumulative_peak_estimate_usd": cumulative, "limit_usd": CAP_USD,
        "authorization_scope": "two_case_live_subset",
        "authorized_case_reserve_usd": float(reserved_case_cap),
        "prior_op3_subset_spend_usd": float(prior_eval_spend),
        "project_remaining_before_run_usd": round(CAP_USD - budget.prior_spent_usd, 8),
        "stopped_before_overrun": stopped})

    report = {"schema_version": 1, "benchmark": "op2_model_paired_subset",
        "evaluation_status": "partial" if has_failures else "completed",
        "run_id": budget.run_id, "started_at": started,
        "runner": "real_chat_model_and_strategy_eval", "model": {"configured_id": configured_model,
            "temperature": 0, "route_token_budget": 2048, "completion_cap": FIXED_COMPLETION_LIMIT,
            "provider_model_ids": sorted(model_ids)},
        "provider_preflight": {"balance_available": balance["is_available"],
            "balance_currencies_available": sorted(item.get("currency", "")
                for item in balance.get("balance_infos", []) if float(item.get("total_balance", 0)) > 0),
            "inference_preflight": "GET /models succeeded"},
        "frozen_dataset": {"spec": str(SUBSET_PATH.relative_to(ROOT)),
            "spec_sha256": frozen["spec_sha256"], "case_count": len(cases),
            "strategies": list(STRATEGIES), "multi_label": "always-multi",
            "cases": [{"id": case["spec"]["id"], "snapshot": case["spec"]["snapshot"],
                "snapshot_sha256": case["snapshot_sha256"], "root_causes": case["spec"]["root_causes"],
                "gold_evidence_ids": sorted(_gold_evidence(case["spec"]["root_causes"], case["sources"]))}
                for case in cases]},
        "budget": {"cap_usd": CAP_USD, "prior_ledger_peak_usd": round(budget.prior_spent_usd, 8),
            "authorization_scope": "two_case_live_subset",
            "preregistered_worst_case_usd": float(reserved_case_cap),
            "project_remaining_before_run_usd": round(CAP_USD - budget.prior_spent_usd, 8),
            "remaining_after_full_reserve_usd": round(CAP_USD - budget.prior_spent_usd - float(reserved_case_cap), 8),
            "prior_op3_subset_spend_usd": float(prior_eval_spend), "actual_run_peak_usd": actual,
            "cumulative_peak_usd": cumulative, "remaining_usd": round(CAP_USD - cumulative, 8),
            "cost_ledger": str(ledger.relative_to(ROOT))},
        "code_sha256": {name: _sha256(ROOT / path) for name, path in {
            "runner": "scripts/evaluate_op2_frozen.py", "strategy_eval": "opspilot/strategy_eval.py",
            "model_client": "opspilot/model_client.py", "gold_scorer": "scripts/evaluate_lab.py"}.items()},
        "request_checkpoint": checkpoint_path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "provider_response_metadata": sorted(provider_response_metadata.values(),
            key=lambda item: (item["case_id"], item["system_sha256"], item["user_payload_sha256"])),
        "request_payload_audit": [*prior_request_audit, *request_audit], "per_case": scored_cases,
        "strategy_summary": summarize_cases(scored_cases),
        "interpretation": "评估哈希校验通过的三份合成冻结快照；独立问题随案例记录，策略接收同一遥测快照，gold 仅用于本地评分。保留请求哈希、时间、模型响应元数据、token 费用与原始非敏感 JSON 响应检查点。本三案小样本不代表泛化或策略整体优劣。"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="send real model requests after preflight")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case-ids", nargs="+", choices=sorted(FROZEN_LIVE_CASES),
        help="all hash-verified cases in the frozen manifest must be listed")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to run the preregistered real-agent comparison")
    try:
        report = run(args.output, case_ids=args.case_ids)
    except Exception as exc:
        print(json.dumps({"status": "stopped_before_or_during_run", "error": type(exc).__name__,
            "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"report": str(args.output), "run_id": report["run_id"],
        "case_count": len(report["per_case"]), "budget": report["budget"],
        "strategy_summary": report["strategy_summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
