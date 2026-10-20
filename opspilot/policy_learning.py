"""One-step contextual bandit policy over OpsPilot investigation routes."""

from __future__ import annotations

import random
from typing import Any

ROUTES = ("rules", "single-agent", "multi-agent")


def context_state(report: dict[str, Any]) -> str:
    status = report.get("status")
    top = report.get("hypotheses", [{}])[0]
    second = report.get("hypotheses", [{}, {}])[1].get("score", 0)
    missing = any(query.get("status") in {"missing", "timeout"} for query in report.get("query_trace", []))
    if missing:
        return "source_incomplete"
    if status == "determined" and top.get("score", 0) - second >= 2:
        return "decisive"
    return "ambiguous"


class ContextualBandit:
    """Sample-mean value learner with forced arm coverage and epsilon exploration."""

    def __init__(self, epsilon: float = 0.1, seed: int = 20261021) -> None:
        self.epsilon = epsilon
        self.random = random.Random(seed)
        self.values: dict[str, dict[str, float]] = {}
        self.counts: dict[str, dict[str, int]] = {}

    def choose(self, state: str, explore: bool = True) -> str:
        values = self.values.setdefault(state, {route: 0.0 for route in ROUTES})
        counts = self.counts.setdefault(state, {route: 0 for route in ROUTES})
        untried = [route for route in ROUTES if counts[route] == 0]
        if explore and untried:
            return untried[0]
        if explore and self.random.random() < self.epsilon:
            return self.random.choice(ROUTES)
        return max(ROUTES, key=lambda route: (values[route], -ROUTES.index(route)))

    def update(self, state: str, route: str, reward: float) -> None:
        if route not in ROUTES:
            raise ValueError(f"unsupported route: {route}")
        values = self.values.setdefault(state, {item: 0.0 for item in ROUTES})
        counts = self.counts.setdefault(state, {item: 0 for item in ROUTES})
        counts[route] += 1
        values[route] += (reward - values[route]) / counts[route]

    def greedy(self, state: str) -> str:
        return self.choose(state, explore=False)

    def to_dict(self) -> dict[str, Any]:
        return {"algorithm": "epsilon_greedy_contextual_bandit_sample_mean", "epsilon": self.epsilon,
                "values": self.values, "counts": self.counts, "routes": list(ROUTES)}

