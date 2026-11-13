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

Writers must build and validate this object before writing a new report. Use
`validate_op_run_artifact(report, is_new_run=True)` at the write boundary. For
historical reports, `is_new_run=False` accepts reports without this field and
validates it when present. Existing generic `run_manifest` schema v1 remains a
separate contract; this addition does not rewrite old reports. `is_new_run=True`
is required at new report write boundaries; `is_new_run=False` preserves
historical report reads.
