# OP evaluation run manifest

New OpsPilot evaluation reports carry a versioned `op_run_manifest` object. It
binds each result to its run, evaluation, dataset, truth labels, evaluator, and
deterministic result fingerprint.

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
  "status": "completed"
}
```

`dataset_sha256` identifies input records. `truth_sha256` identifies labels.
`evaluator_sha256` identifies evaluator code. `result_fingerprint_sha256`
binds deterministic inputs and outputs; machine-dependent timing is excluded.
Identity fields and split must be non-empty. Hashes must be 64 hexadecimal
characters. Status uses the shared run status set.

Writers must build and validate this object before writing a new report. Use
`validate_op_run_artifact(report, is_new_run=True)` at the write boundary. For
historical reports, `is_new_run=False` accepts reports without this field and
validates it when present. Existing generic `run_manifest` schema v1 remains a
separate trace-oriented contract; this addition does not rewrite old reports.
