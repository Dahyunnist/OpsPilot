from datetime import datetime, timedelta, timezone
import unittest

from scripts.prepare_op3_authorized_replay import _validate_preflight_report


class AuthorizedReplayPlanPreflightTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 15, 13, 30, tzinfo=timezone.utc)
        self.report = {
            "started_at": self.now.isoformat(),
            "provider_preflight": {
                "fresh": True,
                "balance_available": True,
                "balance_currencies_available": ["CNY"],
                "inference_preflight": "GET /models succeeded",
            },
            "model": {
                "configured_id": "deepseek-v4-flash",
                "provider_model_ids": ["deepseek-flash"],
            },
            "budget": {"provider_request_attempts": 0, "actual_run_peak_usd": 0.0},
        }

    def test_accepts_recent_balance_and_model_preflight_with_zero_inference(self):
        checked_at, model_ids = _validate_preflight_report(self.report, self.now + timedelta(minutes=5))
        self.assertEqual(self.now.isoformat(), checked_at)
        self.assertEqual(["deepseek-flash"], model_ids)

    def test_rejects_stale_preflight_and_any_prior_inference(self):
        with self.assertRaisesRegex(ValueError, "one-hour freshness"):
            _validate_preflight_report(self.report, self.now + timedelta(hours=1, seconds=1))
        self.report["budget"]["provider_request_attempts"] = 1
        with self.assertRaisesRegex(ValueError, "zero-inference"):
            _validate_preflight_report(self.report, self.now)


if __name__ == "__main__":
    unittest.main()
