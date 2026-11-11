from __future__ import annotations

import unittest

from opspilot.run_manifest import validate_run_manifest


def valid_manifest():
    return {
        "schema_version": 1, "project": "OpsPilot", "git_sha": "a" * 40,
        "dataset_id": "local-lab-v1", "dataset_sha256": "b" * 64, "split": "test",
        "model_id": "none", "prompt_sha": "c" * 64, "tool_schema_sha": "d" * 64,
        "runtime_digest": "python-3.14", "max_steps": 12, "max_tokens": 35000,
        "scorer_sha": "e" * 64, "gold_source": "independent", "cost_usd_estimate": None,
        "started_at": "2026-11-11T00:00:00Z", "ended_at": "2026-11-11T00:01:00+00:00",
        "status": "completed",
    }


class RunManifestTests(unittest.TestCase):
    def test_accepts_versioned_manifest(self):
        self.assertEqual([], validate_run_manifest(valid_manifest()))

    def test_rejects_missing_fields(self):
        manifest = valid_manifest()
        del manifest["dataset_id"]
        self.assertTrue(any("dataset_id" in error for error in validate_run_manifest(manifest)))

    def test_rejects_unknown_version(self):
        manifest = valid_manifest()
        manifest["schema_version"] = 2
        self.assertIn("unsupported schema_version", validate_run_manifest(manifest))

    def test_rejects_invalid_timestamps_and_status(self):
        manifest = valid_manifest()
        manifest.update(started_at="yesterday", status="running")
        errors = validate_run_manifest(manifest)
        self.assertTrue(any("started_at" in error for error in errors))
        self.assertIn("status is not supported", errors)

    def test_rejects_sensitive_fields(self):
        manifest = valid_manifest()
        for key in ("api_key", "access_token", "raw_prompt", "chain_of_thought"):
            candidate = valid_manifest()
            candidate[key] = "must-not-be-stored"
            self.assertTrue(any("sensitive field" in error for error in validate_run_manifest(candidate)), key)

    def test_max_tokens_budget_is_not_sensitive_data(self):
        manifest = valid_manifest()
        self.assertEqual([], validate_run_manifest(manifest))


if __name__ == "__main__":
    unittest.main()
