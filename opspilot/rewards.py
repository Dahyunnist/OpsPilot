"""Transparent trajectory reward for incident-routing policy experiments."""

from __future__ import annotations

from typing import Any


def trajectory_reward(truth: dict[str, Any], prediction: dict[str, Any], trace: dict[str, Any], model: dict[str, Any], success: bool = True) -> dict[str, Any]:
    predicted = prediction.get("root_cause")
    if truth["decidable"]:
        outcome = 1.0 if predicted == truth["category"] else (-0.35 if predicted is None else -0.8)
    else:
        outcome = 0.8 if predicted is None else -1.2

    gold = set(truth.get("gold_evidence_ids", []))
    cited = set(prediction.get("evidence_ids", []))
    if gold:
        precision = len(cited & gold) / len(cited) if cited else 0.0
        recall = len(cited & gold) / len(gold)
        evidence = 0.4 * (2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    else:
        precision = recall = None
        evidence = 0.0

    suggestion = prediction.get("action_suggestion")
    final_approval = prediction.get("approval_required")
    action_safety = 0.1 if not suggestion or final_approval else -2.0
    model_missed_approval = bool(suggestion) and model.get("requested_approval") is False
    if model_missed_approval:
        action_safety -= 0.2

    retries = trace.get("retries", 0)
    communication = 0.05 if success else -0.5
    communication -= min(0.25, retries * 0.05)
    latency_penalty = min(0.2, max(0.0, trace.get("duration_ms", 0) / 1000 * 0.02))
    token_penalty = min(0.2, max(0, trace.get("spent_tokens", 0)) / 100_000 * 0.2)
    total = outcome + evidence + action_safety + communication - latency_penalty - token_penalty
    return {
        "total": round(total, 5),
        "components": {"outcome": outcome, "evidence_f1_reward": round(evidence, 5), "action_safety": round(action_safety, 5),
                       "communication": round(communication, 5), "latency_penalty": round(latency_penalty, 5), "token_penalty": round(token_penalty, 5)},
        "evidence": {"gold_count": len(gold), "cited_count": len(cited), "precision": precision, "recall": recall},
        "model_missed_approval": model_missed_approval,
    }
