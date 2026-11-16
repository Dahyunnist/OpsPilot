"""Shared manifest adapter for offline and synthetic verification reports."""

from __future__ import annotations

from typing import Any

from .run_manifest import build_op_run_manifest


def build_verification_run_manifest(*, run_id: str, evaluation_id: str, dataset_id: str,
                                    dataset_sha256: str | None, result_fingerprint_sha256: str,
                                    git_sha: str, split: str, source_status: str,
                                    evaluator_sha256: str | None, scorer_sha: str | None,
                                    started_at: str | None, ended_at: str | None,
                                    trace_id: str | None, parameters: dict[str, Any]) -> dict[str, Any]:
    """Map verification status and missing facts into the shared OP schema.

    Preserve the producer's semantic status in parameters. Missing provenance
    stays null and receives an explicit not-recorded marker.
    """
    if not isinstance(source_status, str) or not source_status.strip():
        raise ValueError("source_status must be a non-empty string")
    details = dict(parameters)
    details.setdefault("artifact_type", "verification_report")
    details["source_status"] = source_status
    optional_fields = {
        "dataset_sha256": dataset_sha256,
        "evaluator_sha256": evaluator_sha256,
        "scorer_sha": scorer_sha,
        "max_steps": None,
        "started_at": started_at,
        "ended_at": ended_at,
        "trace_id": trace_id,
    }
    for field, value in optional_fields.items():
        if value is None:
            details.setdefault(f"{field}_not_recorded", True)
    lowered_status = source_status.strip().casefold()
    if lowered_status in {"passed", "pass", "verified", "completed", "success"}:
        manifest_status = "completed"
    elif lowered_status in {"cancelled", "canceled"}:
        manifest_status = "cancelled"
    elif lowered_status == "budget_exceeded":
        manifest_status = "budget_exceeded"
    else:
        manifest_status = "failed"
    return build_op_run_manifest(
        project="OpsPilot", run_id=run_id, evaluation_id=evaluation_id,
        dataset_id=dataset_id, dataset_sha256=dataset_sha256, truth_sha256=None,
        split=split, evaluator_sha256=evaluator_sha256,
        result_fingerprint_sha256=result_fingerprint_sha256, git_sha=git_sha,
        model_id="none", prompt_sha=None, tool_schema_sha=None, runtime_digest=None,
        max_steps=None, max_tokens=None, scorer_sha=scorer_sha,
        gold_source="not_applicable", cost_usd_estimate=0.0, trace_id=trace_id,
        started_at=started_at, ended_at=ended_at, parameters=details,
        status=manifest_status,
    )
