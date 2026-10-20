"""Retrieve synthetic training incidents as advisory memory, never as live evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any



def _load_incidents(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            incident = json.loads(line)
            result[incident["incident_id"]] = incident
    return result


def _words(value: Any) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9_]{2,}", json.dumps(value, ensure_ascii=False).lower()))


def retrieve_memories(incident: dict[str, Any], top_k: int = 3, data_dir: Path = Path("data/benchmark")) -> list[dict[str, Any]]:
    train_path = data_dir / "train.jsonl"
    labels_path = data_dir / "labels" / "train.json"
    if not train_path.exists() or not labels_path.exists():
        return []
    history = _load_incidents(train_path)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))["labels"]
    label_by_id = {item["incident_id"]: item for item in labels}
    query = _words({"alert": incident.get("alert"), "timeline": incident.get("timeline"), "signals": incident.get("signals")})
    ranked = []
    for memory_id, prior in history.items():
        common = query & _words({"alert": prior.get("alert"), "timeline": prior.get("timeline"), "signals": prior.get("signals")})
        score = len(common) / max(1, len(query | _words(prior)))
        if score >= 0.01:
            label = label_by_id[memory_id]
            ranked.append({"memory_id": memory_id, "similarity": round(score, 4), "historical_category": label["category"], "historical_conclusion": label["root_cause"], "lesson": "Historical similarity is a retrieval hint, not proof. Live telemetry and contradictions control this investigation."})
    return sorted(ranked, key=lambda item: (-item["similarity"], item["memory_id"]))[:max(0, top_k)]
