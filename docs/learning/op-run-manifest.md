# OP evaluation run manifest

New OpsPilot evaluation reports carry versioned `op_run_manifest`. It shares
the run identity, model, prompt/tool/runtime, budget, scorer, gold source, cost,
time, and trace fields with the general run contract. OP adds evaluation,
dataset/truth hashes, and deterministic result fingerprint.

```json
{
  "schema_version": 1,
  "project": "OpsPilot",
  "run_id": "unique-run-id",
  "evaluation_id": "s15_offline_mock_paired_ablation_v1",
  "dataset_id": "opspilot_causal_chains_v1",
  "dataset_sha256": "<64 hexadecimal characters>",
  "truth_sha256": "<64 hexadecimal characters>",
  "split": "test",
  "evaluator_sha256": "<64 hexadecimal characters>",
  "result_fingerprint_sha256": "<64 hexadecimal characters>",
  "git_sha": "<40 hexadecimal characters>",
  "model_id": "none",
  "prompt_sha": null,
  "tool_schema_sha": null,
  "runtime_digest": null,
  "max_steps": 8,
  "max_tokens": null,
  "scorer_sha": "<64 hexadecimal characters>",
  "gold_source": "independent",
  "cost_usd_estimate": 0.0,
  "trace_id": "unique-trace-id",
  "started_at": "2026-11-13T01:00:00+00:00",
  "ended_at": "2026-11-13T01:01:00+00:00",
  "parameters": {"query_budget": 8, "max_rows_per_source": 100},
  "status": "completed"
}
```

`dataset_sha256` identifies input records. `truth_sha256` identifies labels.
`evaluator_sha256` and `scorer_sha` identify evaluator and scorer code.
`result_fingerprint_sha256` binds deterministic inputs and outputs;
machine-dependent timing is excluded. Git SHA is 40 hex characters. Required
content hashes are 64 hex characters. Identity and trace fields must be
non-empty. Timestamps require timezone and end cannot precede start. Budgets
must be positive integers except `max_tokens` may be null for `model_id=none`.
`parameters` stores the run's fixed evaluation parameters.

For mock/no-model runs, `model_id` is exactly `none`, `gold_source` is
`independent`, and estimated cost is zero. Prompt, tool schema, and runtime
digests must be null because those artifacts do not apply; never invent hashes.
For model-backed runs, model identity is required and those three digests must
be valid SHA-256 values. Unknown model identity is rejected.

S15's `build_s15_op_manifest` maps the evaluator's actual frozen dataset,
labels, evaluator hash, result fingerprint, and parameters into this schema.
The offline regression executes the synthetic evaluator with model calls set
to fail, builds an artifact in memory, and checks each mapped value. It writes
no report.

Writers must build and validate this object before writing a new report. Use
`validate_op_run_artifact(report, is_new_run=True)` at the write boundary. For
historical reports, `is_new_run=False` accepts reports without this field and
validates it when present. Existing generic `run_manifest` schema v1 remains a
separate contract; this addition does not rewrite old reports. `is_new_run=True`
is required at new report write boundaries; `is_new_run=False` preserves
historical report reads.

## Shared public v2 view

`reports/op1-host-matrix-run-manifest.json` is historical source evidence; it remains unchanged. Its normalizer writes `reports/op1-host-matrix-public-manifest.json` in shared field vocabulary aligned with `papertrail/evals/run-manifest-v1.schema.json`, plus explicit environment, field status, source artifact hashes, and trace-event coverage. Schema version 2 allows missing historical facts only as `null` with `blocked`, `not_recorded`, or `not_applicable`; `unknown` placeholders fail validation. `normalizer_sha256` binds the normalization code.

Host manifest audit:

| Field | Recorded value/state |
| --- | --- |
| Run identity | `run_id`, run correlation `trace_id`, source schema version 1 |
| Code/data | 40-hex `git_sha`; `dataset_id=op1-truth-v1`; truth and report SHA-256 verified against files |
| Model | `model_id=none`; model version, prompt and tool hashes are null/not applicable |
| Gold/cost | `gold_source=lab/scenarios/truth.json`, matching truth SHA-256; `cost_usd=0.0`, zero model requests |
| Runtime | Python 3.12, `python:3.12-slim`, Docker CLI 29.1.3 recorded; runtime digest, platform, lock hash and Engine version null/blocked |
| Environment/scorer | Not recorded; null/blocked, never guessed |
| Budget/time | 13 max steps; max tokens not applicable; spend 0.0, cap not recorded; source timestamps retained |
| Result | Source run completed 12/12; public record `status=passed`, `conformance_status=blocked` due missing required provenance |

The source has no standardized `events` array. `timeout`, `retry`, `budget_exceeded`, `unsafe_action_blocked` (safe-action rejection), `cancel`, and `checkpoint_resume` each report count 0 / `not_covered`. This does not claim no similar log or span signal occurred; host telemetry was recorded as `span_observed`, not these standard trace events. Manifest has 48 telemetry spans; their span trace IDs remain distinct from run correlation `trace_id`.

`scripts/normalize_public_host_manifest.py` recreates the safe view and checks source, report, and truth file hashes. `tests/test_public_run_manifest.py` rejects unknown fillers, missing identity, inverted time, sensitive keys, and null scorer hashes without explicit blocked state. The root document named `Agent四项目_JD对标与升级路线` was not present in the accessible workspace; this mapping uses the shared Papertrail schema and field requirements supplied in the task.
