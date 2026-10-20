"""Generate a neutral-ID benchmark for cascades and multiple incident causes."""

from __future__ import annotations

import hashlib
import argparse
import json
import random
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261030
SPLITS = {"train": 10, "validation": 4, "test": 6}
FAMILIES = (
    "pool_root",
    "configuration_root_with_downstream_symptoms",
    "downstream_root_with_pool_pressure",
    "independent_dual_fault",
    "insufficient_or_conflicted",
)
CAUSE_TITLES = {
    "pool_exhaustion": "database connection pool exhausted",
    "config_error": "deployment introduced invalid order service configuration",
    "downstream_timeout": "downstream service exceeded request timeout",
}


class CaseBuilder:
    def __init__(self, rng: random.Random, incident_id: str, index: int) -> None:
        self.rng = rng
        self.incident_id = incident_id
        self.index = index
        self.trace_ids: dict[str, str] = {}
        self.signals: dict[str, list[dict]] = {key: [] for key in ("metrics", "logs", "traces", "deployments")}
        self.cause_evidence: dict[str, list[str]] = {}
        self.causal_edges: list[dict[str, str]] = []
        self.decoys: list[str] = []
        self.span_records: dict[str, str] = {}
        self._serial = 0
        self.day = 1 + (index % 27)

    def _id(self) -> str:
        self._serial += 1
        return f"rec-{self.rng.getrandbits(40):010x}"

    def _trace(self, key: str) -> str:
        return self.trace_ids.setdefault(key, f"{self.rng.getrandbits(128):032x}")

    def _time(self, minute: int, second: int = 0) -> str:
        return f"2027-03-{self.day:02d}T09:{minute:02d}:{second:02d}Z"

    def metric(self, name: str, value: int | float, service: str, minute: int, *, unit: str = "count",
               attrs: dict | None = None, trace: str | None = None, cause: str | None = None) -> str:
        record_id = self._id()
        attributes = {"name": name, "value": value, "unit": unit, "service.name": service}
        if trace:
            attributes["trace_id"] = self._trace(trace)
        attributes.update(attrs or {})
        self.signals["metrics"].append({"record_id": record_id, "timestamp": self._time(minute),
            "service": service, "attributes": attributes})
        if cause:
            self.cause_evidence.setdefault(cause, []).append(record_id)
        return record_id

    def log(self, message: str, service: str, minute: int, *, attrs: dict | None = None,
            trace: str | None = None, cause: str | None = None, decoy: bool = False) -> str:
        record_id = self._id()
        attributes = {"service.name": service}
        if trace:
            attributes["trace_id"] = self._trace(trace)
        attributes.update(attrs or {})
        self.signals["logs"].append({"record_id": record_id, "timestamp": self._time(minute),
            "service": service, "message": message, "attributes": attributes})
        if cause:
            self.cause_evidence.setdefault(cause, []).append(record_id)
        if decoy:
            self.decoys.append(record_id)
        return record_id

    def span(self, name: str, service: str, minute: int, *, status: str = "ok", duration_ms: int = 1,
             trace: str, parent_span_id: str | None = None, peer_service: str | None = None,
             version: str | None = None, cause: str | None = None, decoy: bool = False) -> str:
        record_id = self._id()
        span_id = f"{self.rng.getrandbits(64):016x}"
        attributes = {"trace_id": self._trace(trace), "span_id": span_id, "span": name,
            "status": status, "duration_ms": duration_ms, "service.name": service}
        if parent_span_id:
            attributes["parent_span_id"] = parent_span_id
        if peer_service:
            attributes["peer.service"] = peer_service
        if version:
            attributes["service.version"] = version
        self.signals["traces"].append({"record_id": record_id, "timestamp": self._time(minute),
            "service": service, "attributes": attributes})
        self.span_records[span_id] = record_id
        if cause:
            self.cause_evidence.setdefault(cause, []).append(record_id)
        if decoy:
            self.decoys.append(record_id)
        return span_id

    def deployment(self, service: str, version: str, minute: int, change: str, *, cause: str | None = None,
                   decoy: bool = False) -> str:
        record_id = self._id()
        self.signals["deployments"].append({"record_id": record_id, "timestamp": self._time(minute),
            "service": service, "attributes": {"service.name": service, "version": version,
                "service.version": version, "change": change, "actor": "synthetic-release"}})
        if cause:
            self.cause_evidence.setdefault(cause, []).append(record_id)
        if decoy:
            self.decoys.append(record_id)
        return record_id

    def confounders(self, family: str) -> None:
        # Similar warning words and high pool measurements belong to a separate service/trace.
        candidates = ("search-worker", "billing-worker") if family == "independent_dual_fault" else ("catalog-worker", "search-worker", "billing-worker")
        service = self.rng.choice(candidates)
        trace = f"unrelated-{self.rng.getrandbits(64):016x}"
        self.metric("db_pool_in_use", 38, service, 6, unit="connections",
                    attrs={"limit": 40, "deployment.environment.name": "staging"}, trace=trace, )
        self.log("database pool warning: short queue observed; sampled requests remain healthy",
                 service, 6, attrs={"severity": "warning", "deployment.environment.name": "staging"},
                 trace=trace, decoy=True)
        self.span("db.connection.acquire", service, 6, status="ok", duration_ms=4, trace=trace,
                  peer_service="database", version="worker-v8", decoy=True)
        if family != "pool_root":
            version = f"worker-v{self.rng.randint(5, 9)}"
            self.deployment(service, version, 2, "routine worker release", decoy=True)
            self.log("configuration validation warning from a non-request worker",
                     service, 6, attrs={"severity": "warning", "service.version": version}, decoy=True)

    def add_pool_root(self, service: str = "order", trace: str = "request-main", minute: int = 4) -> None:
        cause = "pool_exhaustion"
        in_use = self.metric("db_pool_in_use", 40, service, minute, unit="connections", attrs={"limit": 40}, trace=trace, cause=cause)
        wait = self.metric("db_pool_wait_ms", 2800, service, minute + 1, unit="ms", trace=trace, cause=cause)
        log = self.log("timed out waiting for database connection", service, minute + 1,
                 attrs={"error": "POOL_ACQUIRE_TIMEOUT"}, trace=trace, cause=cause)
        gateway_span = self.span("gateway.request", "gateway", minute, status="error", duration_ms=3200,
                                 trace=trace, peer_service=service)
        acquire_span = self.span("db.connection.acquire", service, minute + 1, status="error", duration_ms=2800,
                  trace=trace, parent_span_id=gateway_span, peer_service="database", cause=cause)
        self.causal_edges.extend([
            {"from": in_use, "to": wait, "relation": "pool_pressure_precedes_wait"},
            {"from": wait, "to": log, "relation": "same_request_timeout"},
            {"from": self.span_records[gateway_span], "to": self.span_records[acquire_span], "relation": "parent_child_span"},
        ])
        self.metric("database_health", 1, "database", minute + 1, attrs={"health": "healthy"}, trace=trace)

    def add_config_root(self, service: str = "order", trace: str = "request-config", minute: int = 2) -> None:
        cause = "config_error"
        version = f"order-v{self.rng.randint(30, 99)}"
        deploy = self.deployment(service, version, minute, "configuration bundle rollout", cause=cause)
        metric = self.metric("config_validation_errors", 4, service, minute + 1, attrs={"service.version": version},
                    trace=trace, cause=cause)
        log = self.log("required downstream endpoint missing from active configuration", service, minute + 1,
                 attrs={"error": "CONFIG_ENDPOINT_MISSING", "service.version": version}, trace=trace, cause=cause)
        span = self.span("config.validate", service, minute + 1, status="error", duration_ms=3,
                  trace=trace, version=version, cause=cause)
        self.causal_edges.extend([
            {"from": deploy, "to": metric, "relation": "deployment_precedes_validation_failure"},
            {"from": metric, "to": log, "relation": "same_version_validation_error"},
            {"from": log, "to": self.span_records[span], "relation": "same_trace_validation_failure"},
        ])

    def add_downstream_root(self, service: str = "order", trace: str = "request-downstream", minute: int = 3) -> None:
        cause = "downstream_timeout"
        downstream = self.rng.choice(("inventory", "shipping", "pricing"))
        latency = self.metric("request_duration_ms_p95", 3400, downstream, minute, unit="ms", trace=trace, cause=cause)
        timeout_rate = self.metric("downstream_timeout_rate", 0.42, service, minute + 1, unit="ratio", trace=trace, cause=cause)
        log = self.log("downstream request exceeded deadline", service, minute + 1,
                 attrs={"timeout_ms": 2000, "peer.service": downstream}, trace=trace, cause=cause)
        parent = self.span("gateway.request", "gateway", minute, status="error", duration_ms=3900,
                           trace=trace, peer_service=service)
        child = self.span("order.downstream.call", service, minute, status="error", duration_ms=3400,
                  trace=trace, parent_span_id=parent, peer_service=downstream, cause=cause)
        self.causal_edges.extend([
            {"from": latency, "to": self.span_records[child], "relation": "slow_span_confirms_latency"},
            {"from": self.span_records[child], "to": log, "relation": "failed_call_logged"},
            {"from": self.span_records[parent], "to": self.span_records[child], "relation": "parent_child_span"},
            {"from": log, "to": timeout_rate, "relation": "timeout_rate_after_failures"},
        ])

    def finish(self, family: str) -> tuple[dict, dict]:
        self.confounders(family)
        alert = {"name": "http_5xx_rate", "observed_value": 0.34, "timestamp": self._time(7), "unit": "ratio"}
        timeline = [{"event": "gateway 5xx alert crossed the configured threshold", "timestamp": self._time(7)}]
        incident = {"incident_id": self.incident_id, "service": "gateway", "window": {
            "start": self._time(0), "end": self._time(15)}, "alert": alert, "timeline": timeline,
            "signals": self.signals}
        for rows in incident["signals"].values():
            self.rng.shuffle(rows)
        primary_causes = list(self.cause_evidence)
        label = {"incident_id": self.incident_id, "scenario_family": family,
            "decidable": bool(primary_causes), "category": primary_causes[0] if primary_causes else "insufficient_evidence",
            "root_cause": CAUSE_TITLES[primary_causes[0]] if primary_causes else None,
            "primary_causes": primary_causes, "active_causes": primary_causes,
            "cause_evidence_ids": {key: sorted(set(ids)) for key, ids in self.cause_evidence.items()},
            "gold_evidence_ids": sorted({rid for ids in self.cause_evidence.values() for rid in ids}),
            "decoy_evidence_ids": sorted(set(self.decoys)), "causal_edges": self.causal_edges}
        return incident, label


def _unknown_case(builder: CaseBuilder, variant: int) -> None:
    if variant % 2 == 0:
        builder.metric("http_5xx_rate", 0.34, "gateway", 7, unit="ratio")
        builder.log("request failed during a short, unclassified service interruption", "gateway", 7,
                    attrs={"severity": "error"})
        builder.signals["traces"] = []
        builder.signals["deployments"] = []
        builder_status = {"traces": "missing", "deployments": "missing"}
    else:
        builder.metric("config_validation_errors", 1, "order", 6)
        builder.metric("db_pool_in_use", 19, "order", 6, unit="connections", attrs={"limit": 40})
        builder.log("one-off dependency warning with no matching trace", "order", 7,
                    attrs={"severity": "warning"})
        builder_status = {"traces": "missing"}
    builder.cause_evidence.clear()
    builder.causal_edges.clear()
    builder.decoys.extend(row["record_id"] for rows in builder.signals.values() for row in rows)
    builder.source_status = builder_status


def _make_case(rng: random.Random, incident_id: str, family: str, index: int) -> tuple[dict, dict]:
    builder = CaseBuilder(rng, incident_id, index)
    if family == "pool_root":
        builder.add_pool_root()
        # A separate config warning shares time, not the request trace or service version.
        builder.log("configuration warning from an unrelated background worker", "catalog-worker", 5,
                    attrs={"severity": "warning"}, trace="unrelated-config", decoy=True)
    elif family == "configuration_root_with_downstream_symptoms":
        builder.add_config_root()
        builder.metric("http_5xx_rate", 0.45, "gateway", 6, unit="ratio", trace="request-config")
        builder.log("gateway observed failed order response", "gateway", 6, trace="request-config")
        builder.causal_edges.append({"from": builder.cause_evidence["config_error"][0],
            "to": builder.cause_evidence["config_error"][1], "relation": "deployment_precedes_validation_failure"})
    elif family == "downstream_root_with_pool_pressure":
        builder.add_downstream_root()
        pool_id = builder.metric("db_pool_in_use", 38, "order", 5, unit="connections",
            attrs={"limit": 40}, trace="request-downstream")
        wait_id = builder.metric("db_pool_wait_ms", 320, "order", 6, unit="ms", trace="request-downstream")
        builder.log("database acquisition queued while order worker waits on dependency", "order", 6,
            attrs={"severity": "warning"}, trace="request-downstream", decoy=True)
        builder.causal_edges.extend([
            {"from": builder.cause_evidence["downstream_timeout"][-1], "to": pool_id, "relation": "precedes_pool_pressure"},
            {"from": pool_id, "to": wait_id, "relation": "same_request_effect"},
        ])
    elif family == "independent_dual_fault":
        builder.add_config_root("order", "request-config", 2)
        builder.add_pool_root("catalog-worker", "request-pool", 5)
        # The two traces have no causal edge between them.
    else:
        _unknown_case(builder, index)
    incident, label = builder.finish(family)
    if getattr(builder, "source_status", None):
        incident["source_status"] = builder.source_status
    return incident, label


def generate(output: Path = ROOT / "data" / "causal-benchmark", seed: int = SEED) -> dict:
    rng = random.Random(seed)
    rows: dict[str, list[dict]] = {split: [] for split in SPLITS}
    labels: dict[str, list[dict]] = {split: [] for split in SPLITS}
    alphabet = string.ascii_lowercase + string.digits
    serial = 0
    for family in FAMILIES:
        for index in range(sum(SPLITS.values())):
            split = "train" if index < SPLITS["train"] else "validation" if index < SPLITS["train"] + SPLITS["validation"] else "test"
            serial += 1
            incident_id = "opx-" + "".join(rng.choice(alphabet) for _ in range(10))
            incident, label = _make_case(rng, incident_id, family, index)
            rows[split].append(incident)
            labels[split].append(label)

    benchmark_id = "opspilot_causal_chains_v1" if seed == SEED else f"opspilot_causal_chains_seed_{seed}"
    manifest = {"benchmark_id": benchmark_id, "seed": seed, "synthetic": True,
        "cases": sum(len(split_rows) for split_rows in rows.values()),
        "family_counts": {family: sum(item["scenario_family"] == family for split_labels in labels.values() for item in split_labels) for family in FAMILIES},
        "split_policy": "Every family has 10 train, 4 validation, and 6 test cases; labels remain outside incident inputs.",
        "provenance": "Synthetic records authored from the OpsPilot incident contract and OpenTelemetry semantic field conventions; no production or copied incident data.",
        "splits": {}}
    output.mkdir(parents=True, exist_ok=True)
    for split, cases in rows.items():
        cases.sort(key=lambda item: item["incident_id"])
        labels[split].sort(key=lambda item: item["incident_id"])
        data_bytes = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in cases).encode()
        label_bytes = (json.dumps({"labels": labels[split]}, ensure_ascii=False, indent=2) + "\n").encode()
        label_dir = output / "labels"
        label_dir.mkdir(exist_ok=True)
        (output / f"{split}.jsonl").write_bytes(data_bytes)
        (label_dir / f"{split}.json").write_bytes(label_bytes)
        manifest["splits"][split] = {"cases": len(cases),
            "data_sha256": hashlib.sha256(data_bytes).hexdigest(),
            "labels_sha256": hashlib.sha256(label_bytes).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a stratified synthetic causal RCA benchmark")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "causal-benchmark")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.seed), ensure_ascii=False, indent=2))
