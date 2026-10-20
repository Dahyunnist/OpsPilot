"""Typed, auditable messages shared by independent OpsPilot agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class EvidenceRef:
    source: str
    record_id: str


@dataclass(frozen=True)
class AgentMessage:
    task_id: str
    message_id: str
    sender: str
    recipient: str
    kind: Literal["assignment", "finding", "challenge", "handoff", "final"]
    payload: dict[str, Any]
    evidence: tuple[EvidenceRef, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    budget_tokens: int = 0
    attempt: int = 1
    deadline_ms: int = 30_000

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = [asdict(ref) for ref in self.evidence]
        value["allowed_tools"] = list(self.allowed_tools)
        return value


@dataclass
class AgentTrace:
    task_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    spent_tokens: int = 0
    retries: int = 0

    def add(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})

