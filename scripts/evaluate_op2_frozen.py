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
import uuid
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.model_client import ChatModel, load_project_env, redact, usage_token_count  # noqa: E402
from opspilot.live_investigation import investigate_live  # noqa: E402
from opspilot.strategy_eval import (CAUSES, STRATEGIES, FrozenTelemetry, _auto_route,
    _source_for_records, evaluate_snapshot, score_case, summarize_cases)  # noqa: E402
from scripts.evaluate_agents import RunBudget  # noqa: E402
from scripts.evaluate_lab import _gold_evidence  # noqa: E402

SUBSET_PATH = ROOT / "reports" / "op2-model-subset.json"
CAP_USD = 0.60
AUTHORIZATION_SCOPE = "three_case_frozen_subset"
FIXED_WORST_CASE_USD = 0.0230226
FIXED_COMPLETION_LIMIT = 512
MAX_EXPLICIT_RUN_RESERVE_USD = Decimal("0.0230226")
MAX_PROVIDER_PREFLIGHT_AGE_SECONDS = 3600
DEFAULT_OUTPUT = ROOT / "reports" / "op3-op2-subset-evaluation.json"
AUTHORIZED_RETEST_PLAN = ROOT / "reports" / "op3-multiagent-retest-plan-2026-11-15.json"
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


def _total_cap_covers(prior_spent_usd: float, reserve_usd: Decimal, cap_usd: Decimal) -> bool:
    return Decimal(str(prior_spent_usd)) + reserve_usd <= cap_usd


def _validate_new_run_id(run_id: str, ledger: Path) -> None:
    try:
        uuid.UUID(run_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("run_id must be a UUID") from exc
    if ledger.exists():
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(row.get("run_id") == run_id for row in rows):
            raise ValueError("run_id already exists in cost ledger")


def _request_key(case_id: str, system_sha256: str, user_sha256: str, token_budget: int) -> tuple[str, str, str, int]:
    return case_id, system_sha256, user_sha256, token_budget


def _prior_reports(output: Path) -> list[Path]:
    return sorted(path for path in (ROOT / "reports").glob("op3-op2-*.json")
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


def _checkpoint_response_has_usable_usage(response: Any) -> bool:
    """Reject paid checkpoints whose redacted or malformed usage cannot be scored safely."""
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return False
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return False
    try:
        prompt_tokens = usage_token_count(usage.get("prompt_tokens"), "prompt_tokens")
        completion_tokens = usage_token_count(usage.get("completion_tokens"), "completion_tokens")
        total_tokens = usage_token_count(usage.get("total_tokens"), "total_tokens")
        if total_tokens != prompt_tokens + completion_tokens:
            return False
        details = usage.get("prompt_tokens_details")
        if isinstance(details, str):
            details = json.loads(details)
        if details is not None and not isinstance(details, dict):
            return False
        if isinstance(details, dict):
            cached_tokens = usage_token_count(details.get("cached_tokens"), "cached_tokens")
            if cached_tokens > prompt_tokens:
                return False
    except (ValueError, json.JSONDecodeError):
        return False
    return True


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
            if status == "completed":
                response = record.get("response")
                if _checkpoint_response_has_usable_usage(response):
                    responses[key] = response
                    uncertain.discard(key)
                else:
                    responses.pop(key, None)
                    uncertain.add(key)
            elif status in {"pending", "failed_or_blocked"}:
                responses.pop(key, None)
                uncertain.add(key)
    return responses, uncertain


def _unusable_paid_prompt_keys(output: Path) -> list[dict[str, Any]]:
    prior_reports = _prior_reports(output)
    completed, _audit = _prior_completed_requests(prior_reports)
    checkpoint_paths = sorted((ROOT / "reports").glob("op3-op2-*.requests.jsonl"))
    responses, uncertain = _load_request_checkpoints(checkpoint_paths)
    keys = (completed | uncertain) - set(responses)
    return [{"case_id": key[0], "system_sha256": key[1], "user_payload_sha256": key[2],
        "token_budget": key[3]} for key in sorted(keys)]


def _checkpoint_inventory() -> list[dict[str, Any]]:
    rows = []
    for path in sorted((ROOT / "reports").glob("op3-op2-*.requests.jsonl")):
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows.append({"path": path.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": _sha256(path), "record_count": len(records)})
    return rows


def _blocked_artifact_inventory() -> list[dict[str, str]]:
    paths = [
        ROOT / "reports" / "op3-multiagent-retest-plan-2026-11-15.json",
        ROOT / "reports" / "op3-multiagent-execution-blocked-2026-11-15.json",
        ROOT / "reports" / "op3-multiagent-run-blocked-approval-review-2026-11-15.json",
        ROOT / "reports" / "op3-multiagent-final-6b03e236-6a28-4f40-b752-a6325f522cb3.json",
    ]
    rows = []
    for path in paths:
        if not path.is_file():
            raise ValueError(f"required prior blocked artifact is missing: {path.name}")
        rows.append({"path": path.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": _sha256(path)})
    return rows


def _refuse_unavailable_paid_response(
        key: tuple[str, str, str, int],
        prior_completed_requests: set[tuple[str, str, str, int]],
        checkpoint_uncertain: set[tuple[str, str, str, int]]) -> None:
    if key in prior_completed_requests or key in checkpoint_uncertain:
        raise PriorPaidResponseUnavailable(
            "prior paid response is unavailable or unusable; refusing duplicate inference")


def _check_prior_paid_replay_key(key: tuple[str, str, str, int],
        prior_paid_keys: set[tuple[str, str, str, int]],
        authorized_replay_keys: set[tuple[str, str, str, int]]) -> bool:
    if key not in prior_paid_keys:
        return False
    if key not in authorized_replay_keys:
        raise ValueError("unusable paid prompt is outside the exact authorized replay key list")
    return True


def _append_request_checkpoint(path: Path, record: dict[str, Any], lock: threading.Lock) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock, path.open("a", encoding="utf-8") as handle:
        if record.get("status") == "completed" and path.exists():
            key = _request_key(record["case_id"], record["system_sha256"],
                record["user_payload_sha256"], int(record["token_budget"]))
            handle.flush()
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                prior = json.loads(line)
                if prior.get("status") == "completed" and _request_key(prior["case_id"],
                        prior["system_sha256"], prior["user_payload_sha256"],
                        int(prior["token_budget"])) == key:
                    return
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


def run(output: Path, case_ids: list[str] | None = None,
        strategies: tuple[str, ...] = STRATEGIES,
        max_run_reserve_usd: Decimal | None = None,
        preflight_only: bool = False, run_id: str | None = None,
        allow_authorized_paid_replay: bool = False,
        historical_provider_preflight: Path | None = None,
        authorized_replay_plan_path: Path | None = None,
        authorized_replay_plan_sha256: str | None = None) -> dict[str, Any]:
    if not preflight_only and output.exists():
        raise FileExistsError(f"refusing to overwrite prior evaluation report: {output.name}")
    if case_ids is None or set(case_ids) != set(FROZEN_LIVE_CASES) or len(case_ids) != len(FROZEN_LIVE_CASES):
        raise ValueError("live execution requires every case in the hash-verified frozen manifest")
    _validate_strategy_selection(strategies)
    if max_run_reserve_usd is not None and max_run_reserve_usd > MAX_EXPLICIT_RUN_RESERVE_USD:
        raise ValueError("explicit run reserve exceeds the authorized $0.0230226 maximum")
    checkpoint_path = _checkpoint_path(output)
    if not preflight_only and checkpoint_path.exists():
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
    run_id = run_id or str(uuid.uuid4())
    _validate_new_run_id(run_id, ledger)
    prior_eval_spend = _prior_subset_spend(ledger)
    reserved_case_cap = (max_run_reserve_usd if max_run_reserve_usd is not None else
        sum((Decimal(str(case["spec"]["worst_case_usd"])) for case in cases), Decimal("0")))
    if reserved_case_cap <= 0:
        raise ValueError("run reserve must be positive")
    budget = RunBudget(CAP_USD, ledger, configured_model, run_limit_usd=float(reserved_case_cap), run_id=run_id)
    if not _total_cap_covers(budget.prior_spent_usd, reserved_case_cap, Decimal(str(CAP_USD))):
        raise ValueError("remaining project cap cannot cover the complete selected-case peak reserve; no inference calls sent")
    request_plan = _planned_requests(cases, strategies)
    authorized_replay_plan = None
    historical_preflight = None
    if allow_authorized_paid_replay:
        if preflight_only:
            raise ValueError("authorized paid replay requires an execution run, not preflight-only mode")
        if historical_provider_preflight is None or authorized_replay_plan_path is None:
            raise ValueError("authorized paid replay requires its versioned plan and timestamped preflight evidence")
        if not authorized_replay_plan_sha256:
            raise ValueError("authorized paid replay requires the independently supplied plan SHA-256")
        authorized_replay_plan = _validate_authorized_retest_plan(authorized_replay_plan_path,
            output, run_id, frozen, cases, strategies, reserved_case_cap, request_plan,
            budget.prior_spent_usd, authorized_replay_plan_sha256)
        historical_preflight = _load_historical_provider_preflight(historical_provider_preflight,
            authorized_replay_plan, configured_model)
    elif any((historical_provider_preflight, authorized_replay_plan_path, authorized_replay_plan_sha256)):
        raise ValueError("replay plan and provider evidence require --allow-authorized-paid-replay")

    prior_report_paths = _prior_reports(output)
    prior_completed_requests, prior_request_audit = _prior_completed_requests(prior_report_paths)
    selected_ids = set(case_ids)
    prior_completed_requests = {key for key in prior_completed_requests if key[0] in selected_ids}
    prior_request_audit = [item for item in prior_request_audit if item.get("case_id") in selected_ids]
    reusable_checkpoints = sorted(path for path in ledger.parent.glob("op3-op2-*.requests.jsonl")
        if path.resolve() != checkpoint_path.resolve())
    checkpoint_responses, checkpoint_uncertain = _load_request_checkpoints([*reusable_checkpoints, checkpoint_path])
    checkpoint_responses = {key: value for key, value in checkpoint_responses.items() if key[0] in selected_ids}
    checkpoint_uncertain = {key for key in checkpoint_uncertain if key[0] in selected_ids}
    authorized_replay_keys = ({(item["case_id"], item["system_sha256"],
        item["user_payload_sha256"], int(item["token_budget"]))
        for item in authorized_replay_plan.get("resume_policy", {}).get("unusable_prior_paid_prompt_keys", [])}
        if authorized_replay_plan is not None else set())

    provider_preflight_observed_at = datetime.now(timezone.utc).isoformat()
    if historical_preflight is not None:
        model_ids = set(historical_preflight["provider_model_ids"])
        provider_preflight_details = {"balance_available": True,
            "balance_currencies_available": historical_preflight["balance_currencies_available"],
            "inference_preflight": "historical GET /models succeeded",
            "fresh": False, "source": historical_preflight["source"],
            "source_sha256": historical_preflight["source_sha256"],
            "observed_at": historical_preflight["observed_at"]}
    else:
        # These authenticated GETs are free preflight calls. No inference request is sent here.
        balance = _json_request("https://api.deepseek.com/user/balance", api_key)
        models = _json_request("https://api.deepseek.com/models", api_key)
        if balance.get("is_available") is not True:
            raise ValueError("provider reports insufficient balance; no inference calls sent")
        if not any(float(item.get("total_balance", 0)) > 0 for item in balance.get("balance_infos", [])):
            raise ValueError("provider balance is zero; no inference calls sent")
        model_ids = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
        provider_preflight_details = {"balance_available": True,
            "balance_currencies_available": sorted(item.get("currency", "")
                for item in balance.get("balance_infos", []) if float(item.get("total_balance", 0)) > 0),
            "inference_preflight": "GET /models succeeded", "fresh": True}
    if not model_ids or not model_ids.intersection({"deepseek-flash", "deepseek-v4-flash"}):
        raise ValueError("provider does not list the frozen Flash model family; no inference calls sent")
    if preflight_only:
        return {"status": "preflight_passed", "run_id": run_id, "provider_preflight": provider_preflight_details,
            "started_at": provider_preflight_observed_at,
            "model": {"configured_id": configured_model, "provider_model_ids": sorted(model_ids)},
            "frozen_dataset": {"spec": str(SUBSET_PATH.relative_to(ROOT)),
                "spec_sha256": frozen["spec_sha256"], "case_count": len(cases),
                "strategies": list(strategies), "cases": [{"id": case["spec"]["id"],
                    "snapshot_sha256": case["snapshot_sha256"]} for case in cases]},
            "provider_model_ids": sorted(model_ids), "strategies": list(strategies),
            "case_ids": [case["spec"]["id"] for case in cases],
            "case_sha256": {case["spec"]["id"]: case["snapshot_sha256"] for case in cases},
            "budget": {"provider_request_attempts": 0, "actual_run_peak_usd": 0.0,
                "prior_ledger_peak_usd": round(budget.prior_spent_usd, 8),
                "max_run_reserve_usd": float(reserved_case_cap),
                "cumulative_worst_case_usd": round(budget.prior_spent_usd + float(reserved_case_cap), 8),
                "project_cap_usd": CAP_USD},
            "prior_ledger_peak_usd": round(budget.prior_spent_usd, 8),
            "max_run_reserve_usd": float(reserved_case_cap),
            "cumulative_worst_case_usd": round(budget.prior_spent_usd + float(reserved_case_cap), 8),
            "project_cap_usd": CAP_USD, "inference_calls": 0,
            "request_plan": _planned_requests(cases, strategies)}

    # Frozen registry caps completions at 512 regardless of the wider .env setting.
    os.environ["LLM_MAX_COMPLETION_TOKENS"] = str(FIXED_COMPLETION_LIMIT)
    model = ChatModel()
    started = datetime.now(timezone.utc).isoformat()
    request_audit: list[dict[str, Any]] = []
    audit_lock = threading.Lock()
    request_attempt_lock = threading.Lock()
    provider_request_attempts = 0
    checkpoint_lock = threading.Lock()
    in_run_responses: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    provider_response_metadata: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    scored_cases = []
    for case in cases:
        records_by_id = {row["record_id"]: row for result in case["sources"].values()
            for row in result.get("records", [])}

        def audited_call(operation: Any, instance: Any, system: str, user: str, token_budget: int) -> dict[str, Any]:
            _validate_prompt_payload(system, user, records_by_id)
            system_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
            user_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
            key = _request_key(case["spec"]["id"], system_hash, user_hash, token_budget)
            model_cap = min(instance.max_tokens, max(64, token_budget))
            request_body = {"model": configured_model, "temperature": 0, "max_tokens": model_cap,
                **({"reasoning_effort": "none"} if configured_model.startswith("deepseek-v4") else {}),
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            request_body_hash = hashlib.sha256(json.dumps(request_body).encode("utf-8")).hexdigest()
            request_payload = json.loads(user)
            if authorized_replay_plan is not None:
                review = authorized_replay_plan["_validated_payload_review"]
                static_keys = {(item["case_id"], item["system_sha256"],
                    item["user_payload_sha256"], item["token_budget"]): item["request_body_sha256"]
                    for item in review["static_request_hashes"]}
                if "experts" in request_payload:
                    expected_adjudicator_hash = hashlib.sha256(
                        review["system_prompts"]["adjudicator"].encode("utf-8")).hexdigest()
                    dynamic_allowlist = {item["case_id"]: item for item in
                        authorized_replay_plan["prompt_review"]["dynamic_adjudicator_templates"]}
                    dynamic = dynamic_allowlist.get(case["spec"]["id"], {})
                    expected_ids = sorted(records_by_id)[:1000]
                    expected_sources = sorted(source for source, result in case["sources"].items()
                        if result.get("records"))
                    actual_sources = sorted(item.get("source") for item in request_payload.get("experts", []))
                    if (system_hash != expected_adjudicator_hash
                            or dynamic.get("system_sha256") != system_hash
                            or dynamic.get("token_budget") != token_budget
                            or request_payload.get("all_evidence_ids") != expected_ids
                            or actual_sources != expected_sources):
                        raise ValueError("adjudicator payload differs from authorized source scope")
                elif (key not in static_keys or static_keys[key] != request_body_hash):
                    raise ValueError("static prompt hash differs from authorized payload review")
            with audit_lock:
                prior_result = in_run_responses.get(key)
                if prior_result is not None:
                    request_audit.append({"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "request_body_sha256": request_body_hash,
                        "token_budget": token_budget, "status": "reused_in_run"})
                    return prior_result
                prior_checkpoint = checkpoint_responses.get(key)
                if prior_checkpoint is not None:
                    request_audit.append({"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "request_body_sha256": request_body_hash,
                        "token_budget": token_budget, "status": "reused_checkpoint"})
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
                "request_body_sha256": request_body_hash,
                "token_budget": token_budget, "status": "pending"}
            prior_paid_keys = prior_completed_requests | checkpoint_uncertain
            if key in prior_paid_keys:
                if authorized_replay_plan is None:
                    audit["previous_paid_response_unavailable"] = True
                    audit["status"] = "previous_paid_response_unavailable"
                    with audit_lock:
                        request_audit.append(audit)
                    _refuse_unavailable_paid_response(key, prior_completed_requests, checkpoint_uncertain)
                _check_prior_paid_replay_key(key, prior_paid_keys, authorized_replay_keys)
                audit["previous_paid_response_unavailable"] = True
                audit["prior_paid_replay_authorized"] = True
            with audit_lock:
                request_audit.append(audit)

            request_record = {"case_id": key[0], "system_sha256": key[1],
                "user_payload_sha256": key[2], "request_body_sha256": request_body_hash,
                "token_budget": token_budget, "run_id": budget.run_id}

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
                return response

            nonlocal provider_request_attempts
            with request_attempt_lock:
                if provider_request_attempts >= request_plan["max_provider_requests"]:
                    raise ValueError("provider request attempt limit exceeded")
                provider_request_attempts += 1
            try:
                result = budget.call(checkpointed_operation, instance, system, user, token_budget,
                    request_metadata={"case_id": key[0], "system_sha256": key[1],
                        "user_payload_sha256": key[2], "token_budget": token_budget,
                        "request_body_sha256": request_body_hash,
                        "request_scope": budget.run_id,
                        "prior_paid_replay_authorized": key in authorized_replay_keys})
            except Exception as exc:
                audit.update({"status": "failed_or_blocked", "error_type": type(exc).__name__})
                raise
            _append_request_checkpoint(checkpoint_path,
                {**request_record, "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
                    "response": redact(result)}, checkpoint_lock)
            audit["status"] = "completed"
            with audit_lock:
                in_run_responses[key] = result
                provider_response_metadata[key] = {"case_id": key[0], "system_sha256": key[1],
                    "user_payload_sha256": key[2], "provider_model": result.get("provider_model"),
                    "provider_system_fingerprint": result.get("provider_system_fingerprint"),
                    "source": "new_provider_response"}
            return result

        report = evaluate_snapshot(case["spec"]["id"], case["start"], case["end"],
            case["sources"], model, audited_call, strategies, token_budget=2048,
            retry_truncated=False)
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
        "benchmark": "opspilot_op2_frozen_real_agent", "strategies": list(strategies),
        "case_ids": [case["spec"]["id"] for case in cases], "peak_rate_estimate_usd": actual,
        "provider_rate_estimate_usd": round(sum(item["provider_rate_estimate_usd"]
            for case in scored_cases for item in case["strategies"].values()), 8),
        "cumulative_peak_estimate_usd": cumulative, "limit_usd": CAP_USD,
        "authorization_scope": f"{AUTHORIZATION_SCOPE}_{'_'.join(strategies)}",
        "authorized_case_reserve_usd": float(reserved_case_cap),
        "authorized_prior_paid_replay": authorized_replay_plan is not None,
        "provider_request_attempts": provider_request_attempts,
        "prior_op3_subset_spend_usd": float(prior_eval_spend),
        "project_remaining_before_run_usd": round(CAP_USD - budget.prior_spent_usd, 8),
        "stopped_before_overrun": stopped})

    report = {"schema_version": 1, "benchmark": "op2_model_paired_subset",
        "evaluation_status": "partial" if has_failures else "completed",
        "run_id": budget.run_id, "started_at": started,
        "runner": "real_chat_model_and_strategy_eval", "model": {"configured_id": configured_model,
            "temperature": 0, "route_token_budget": 2048, "completion_cap": FIXED_COMPLETION_LIMIT,
            "provider_model_ids": sorted(model_ids)},
        "provider_preflight": provider_preflight_details,
        "frozen_dataset": {"spec": str(SUBSET_PATH.relative_to(ROOT)),
            "spec_sha256": frozen["spec_sha256"], "case_count": len(cases),
            "strategies": list(strategies), "multi_label": "always-multi",
            "cases": [{"id": case["spec"]["id"], "snapshot": case["spec"]["snapshot"],
                "snapshot_sha256": case["snapshot_sha256"], "root_causes": case["spec"]["root_causes"],
                "gold_evidence_ids": sorted(_gold_evidence(case["spec"]["root_causes"], case["sources"]))}
                for case in cases]},
        "budget": {"cap_usd": CAP_USD, "prior_ledger_peak_usd": round(budget.prior_spent_usd, 8),
            "authorization_scope": f"{AUTHORIZATION_SCOPE}_{'_'.join(strategies)}",
            "preregistered_worst_case_usd": float(reserved_case_cap),
            "project_remaining_before_run_usd": round(CAP_USD - budget.prior_spent_usd, 8),
            "remaining_after_full_reserve_usd": round(CAP_USD - budget.prior_spent_usd - float(reserved_case_cap), 8),
            "prior_op3_subset_spend_usd": float(prior_eval_spend), "actual_run_peak_usd": actual,
            "cumulative_peak_usd": cumulative, "remaining_usd": round(CAP_USD - cumulative, 8),
            "cost_ledger": str(ledger.relative_to(ROOT)),
            "provider_request_attempts": provider_request_attempts,
            "max_provider_requests": request_plan["max_provider_requests"],
            "authorized_prior_paid_replay": authorized_replay_plan is not None},
        "authorization": ({"manifest": authorized_replay_plan_path.resolve().relative_to(ROOT.resolve()).as_posix(),
            "manifest_sha256": authorized_replay_plan_sha256,
            "status": "authorized_exact_scope",
            "authorized_unusable_prior_paid_prompt_keys": len(authorized_replay_keys),
            "provider_preflight_source": historical_preflight.get("source"),
            "provider_preflight_sha256": historical_preflight.get("source_sha256"),
            "provider_preflight_observed_at": historical_preflight.get("observed_at")}
            if authorized_replay_plan is not None else None),
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
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES),
        help="strategies to evaluate; omitted means all preregistered strategies")
    parser.add_argument("--max-run-reserve-usd", type=Decimal,
        help="hard run reserve ceiling, enforced before each request")
    parser.add_argument("--preflight-only", action="store_true",
        help="verify frozen hashes, budget and provider without inference or report writes")
    parser.add_argument("--run-id", help="UUID shared by preflight and execution")
    parser.add_argument("--allow-authorized-paid-replay", action="store_true",
        help="permit re-requesting prior paid prompts only when a versioned exact-scope authorization plan matches")
    parser.add_argument("--historical-provider-preflight", type=Path,
        help="use timestamped provider evidence named by the exact replay plan; never refreshed by this option")
    parser.add_argument("--authorized-replay-plan", type=Path,
        help="versioned authorization plan for this exact run ID, output, hashes, strategy and paid-key allowlist")
    parser.add_argument("--authorized-replay-plan-sha256",
        help="expected SHA-256 of the authorization plan, supplied separately to detect plan edits")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to run the preregistered real-agent comparison")
    try:
        report = run(args.output, case_ids=args.case_ids, strategies=tuple(args.strategies),
            max_run_reserve_usd=args.max_run_reserve_usd, preflight_only=args.preflight_only,
            run_id=args.run_id, allow_authorized_paid_replay=args.allow_authorized_paid_replay,
            historical_provider_preflight=args.historical_provider_preflight,
            authorized_replay_plan_path=args.authorized_replay_plan,
            authorized_replay_plan_sha256=args.authorized_replay_plan_sha256)
    except Exception as exc:
        print(json.dumps({"status": "stopped_before_or_during_run", "error": type(exc).__name__,
            "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report if args.preflight_only else {"report": str(args.output), "run_id": report["run_id"],
        "case_count": len(report["per_case"]), "budget": report["budget"],
        "strategy_summary": report["strategy_summary"]}, ensure_ascii=False, indent=2))
    return 0


def _validate_strategy_selection(strategies: tuple[str, ...]) -> None:
    if not strategies or any(strategy not in STRATEGIES for strategy in strategies):
        raise ValueError("unsupported or empty strategy selection")
    if len(set(strategies)) != len(strategies):
        raise ValueError("duplicate strategy selection")


def _planned_requests(cases: list[dict[str, Any]], strategies: tuple[str, ...]) -> dict[str, Any]:
    per_case = []
    total = 0
    for case in cases:
        sources = _source_for_records(case["sources"])
        source_count = sum(bool(rows) for rows in sources.values())
        live = investigate_live(case["start"], case["end"], telemetry=FrozenTelemetry(case["sources"]))
        auto_route = _auto_route(live)
        routes = {strategy: auto_route if strategy == "auto" else strategy for strategy in strategies}
        unique_paid_routes = {route for route in routes.values() if route in {"single", "multi"}}
        calls_by_route = {"single": 1, "multi": source_count + 1}
        planned = sum(calls_by_route[route] for route in unique_paid_routes)
        total += planned
        per_case.append({"case_id": case["spec"]["id"], "auto_route": auto_route,
            "nonempty_sources": source_count, "selected_routes": routes,
            "max_provider_requests": planned})
    return {"count_basis": "unique prompts across selected routes; truncation retries disabled",
        "per_case": per_case, "max_provider_requests": total}


def _validate_authorized_retest_plan(plan_path: Path, output: Path, run_id: str,
        frozen: dict[str, Any], cases: list[dict[str, Any]], strategies: tuple[str, ...],
        reserve_usd: Decimal, request_plan: dict[str, Any], prior_peak_usd: float,
        expected_plan_sha256: str | None = None) -> dict[str, Any]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("authorized replay plan is unreadable") from exc
    authorization = plan.get("authorization", {})
    expected_cases = [case["spec"]["id"] for case in cases]
    expected_hashes = {case["spec"]["id"]: case["snapshot_sha256"] for case in cases}
    report_rel = lambda path: path.resolve().relative_to(ROOT.resolve()).as_posix()
    checks = [
        plan.get("plan_schema_version") == 2,
        plan.get("artifact_type") == "authorized_paid_replay_plan_v2",
        plan.get("status") == "authorized_for_execution",
        expected_plan_sha256 is not None and _sha256(plan_path) == expected_plan_sha256,
        plan.get("plan_path") == report_rel(plan_path),
        authorization.get("user_confirmed") is True,
        authorization.get("source") == "explicit current user instruction",
        authorization.get("run_id") == run_id == plan.get("run_id"),
        plan.get("planned_output") == report_rel(output),
        plan.get("planned_checkpoint") == report_rel(_checkpoint_path(output)),
        plan.get("scope", {}).get("spec_sha256") == frozen["spec_sha256"],
        plan.get("scope", {}).get("case_ids") == expected_cases,
        plan.get("scope", {}).get("snapshot_sha256") == expected_hashes,
        plan.get("scope", {}).get("strategies") == list(strategies) == ["rules", "single", "multi", "auto"],
        plan.get("scope", {}).get("retries_enabled") is False,
        authorization.get("case_ids") == expected_cases,
        authorization.get("snapshot_sha256") == expected_hashes,
        authorization.get("strategies") == list(strategies),
        authorization.get("max_provider_requests") == request_plan["max_provider_requests"] == 18,
        authorization.get("reserve_usd") == float(reserve_usd) == float(FIXED_WORST_CASE_USD),
        plan.get("request_plan", {}).get("max_provider_requests") == request_plan["max_provider_requests"],
        plan.get("request_plan") == request_plan,
        plan.get("budget", {}).get("latest_ledger_peak_usd") == round(prior_peak_usd, 8),
        plan.get("budget", {}).get("prior_op3_subset_spend_usd") == float(_prior_subset_spend(
            ROOT / "reports" / "api-cost-ledger.jsonl")),
        plan.get("budget", {}).get("cumulative_worst_case_usd") == round(prior_peak_usd + float(reserve_usd), 8),
        plan.get("budget", {}).get("ledger_sha256") == _sha256(ROOT / "reports" / "api-cost-ledger.jsonl"),
        plan.get("budget", {}).get("within_project_cap") is True,
        plan.get("data_safety", {}).get("synthetic_frozen_snapshots_only") is True,
        plan.get("data_safety", {}).get("gold_sent_to_provider") is False,
        plan.get("data_safety", {}).get("secrets_or_credentials_written") is False,
        plan.get("resume_policy", {}).get("reuse_requires_verified_usage") is True,
        plan.get("resume_policy", {}).get("unusable_checkpoint_action") == "reissue_only_if_key_is_explicitly_authorized",
        plan.get("reserve_policy", {}).get("lock") == "RunBudget per-request lock",
        plan.get("reserve_policy", {}).get("max_provider_requests") == 18,
        plan.get("reserve_policy", {}).get("max_run_reserve_usd") == float(reserve_usd),
        plan.get("provider_preflight_evidence", {}).get("max_age_seconds") == MAX_PROVIDER_PREFLIGHT_AGE_SECONDS,
        authorization.get("allow_unusable_prior_paid_prompt_keys_only") == hashlib.sha256(
            json.dumps(plan.get("resume_policy", {}).get("unusable_prior_paid_prompt_keys", []),
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    ]
    if not all(checks):
        failed = [str(index) for index, passed in enumerate(checks) if not passed]
        raise ValueError("authorized replay plan differs from run id, frozen payload, route, or budget; checks="
            + ",".join(failed))
    if plan.get("checkpoint_inventory") != _checkpoint_inventory():
        raise ValueError("checkpoint inventory changed after authorized plan preparation")
    if plan.get("resume_policy", {}).get("unusable_prior_paid_prompt_keys") != _unusable_paid_prompt_keys(output):
        raise ValueError("unusable paid prompt keys differ from the exact authorization allowlist")
    if plan.get("prior_blocked_artifacts") != _blocked_artifact_inventory():
        raise ValueError("prior blocked artifacts changed after authorized plan preparation")
    prompt_review_ref = plan.get("prompt_review", {})
    expected_review_path = ROOT / "reports" / "op3-multiagent-payload-review-2026-11-15.json"
    if prompt_review_ref.get("artifact") != expected_review_path.relative_to(ROOT).as_posix():
        raise ValueError("authorized payload review path differs from plan")
    if _sha256(expected_review_path) != prompt_review_ref.get("sha256"):
        raise ValueError("authorized payload review hash differs from plan")
    try:
        payload_review = json.loads(expected_review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("authorized payload review is unreadable") from exc
    static_requests = payload_review.get("static_request_hashes", [])
    if (payload_review.get("status") != "verified_frozen_inputs_and_prompt_contract"
            or payload_review.get("spec_sha256") != frozen["spec_sha256"]
            or payload_review.get("request_plan_max_provider_requests") != 18
            or payload_review.get("worst_case_reserve_usd") != float(reserve_usd)
            or len(static_requests) != 15
            or payload_review.get("dynamic_adjudicator_count") != 3
            or payload_review.get("validation", {}).get("gold_in_payload") is not False
            or payload_review.get("validation", {}).get("credentials_in_payload") is not False
            or payload_review.get("validation", {}).get("real_customer_data_in_payload") is not False):
        raise ValueError("authorized payload review differs from frozen scope or send limits")
    if (prompt_review_ref.get("static_request_hashes") != static_requests
            or prompt_review_ref.get("dynamic_adjudicator_templates") != payload_review.get("dynamic_adjudicator_templates")):
        raise ValueError("authorized prompt hashes differ from payload review")
    expected_code_hashes = {
        "runner": "scripts/evaluate_op2_frozen.py",
        "strategy_eval": "opspilot/strategy_eval.py",
        "model_client": "opspilot/model_client.py",
        "gold_scorer": "scripts/evaluate_lab.py",
        "plan_builder": "scripts/prepare_op3_authorized_replay.py",
    }
    if plan.get("code_sha256") != {name: _sha256(ROOT / path) for name, path in expected_code_hashes.items()}:
        raise ValueError("runner, strategy, model, scorer or plan-builder code changed after authorization")
    plan["_validated_payload_review"] = payload_review
    return plan


def _load_historical_provider_preflight(path: Path, plan: dict[str, Any], model_id: str) -> dict[str, Any]:
    evidence = plan.get("provider_preflight_evidence", {})
    if path.resolve().relative_to(ROOT.resolve()).as_posix() != evidence.get("source"):
        raise ValueError("historical provider preflight path differs from approved plan")
    if _sha256(path) != evidence.get("source_sha256"):
        raise ValueError("historical provider preflight report hash differs from approved plan")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical provider preflight report is unreadable") from exc
    checked_at = report.get("started_at")
    preflight = report.get("provider_preflight", {})
    models = report.get("model", {}).get("provider_model_ids", [])
    if (checked_at != evidence.get("observed_at")
            or report.get("run_id") != evidence.get("source_run_id")
            or preflight.get("balance_available") is not True):
        raise ValueError("historical provider balance evidence does not match approved plan")
    observed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    age_seconds = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if age_seconds < 0 or age_seconds > int(evidence.get("max_age_seconds", 0)):
        raise ValueError("provider preflight evidence is outside its authorized freshness window")
    if preflight.get("inference_preflight") != "GET /models succeeded":
        raise ValueError("historical model-list preflight did not succeed")
    if report.get("model", {}).get("configured_id") != model_id:
        raise ValueError("historical provider preflight used a different configured model")
    if not set(models).intersection({"deepseek-flash", "deepseek-v4-flash"}):
        raise ValueError("historical provider model list does not include the approved model family")
    if preflight.get("fresh") is not True or "CNY" not in preflight.get("balance_currencies_available", []):
        raise ValueError("provider preflight does not prove a fresh available CNY balance")
    return {"observed_at": checked_at, "balance_available": True,
        "balance_currencies_available": preflight.get("balance_currencies_available", []),
        "provider_model_ids": models, "fresh": False,
        "source": path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "source_sha256": evidence["source_sha256"]}


if __name__ == "__main__":
    raise SystemExit(main())
