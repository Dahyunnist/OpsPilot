import unittest

from opspilot.policy_learning import ContextualBandit
from opspilot.rewards import trajectory_reward


class PolicyLearningTests(unittest.TestCase):
    def test_bandit_explores_each_route_then_updates_sample_mean(self):
        policy = ContextualBandit(epsilon=0, seed=1)
        actions = []
        for reward in (0.2, 0.3, 0.1):
            action = policy.choose("decisive")
            actions.append(action)
            policy.update("decisive", action, reward)
        self.assertEqual(["rules", "single-agent", "multi-agent"], actions)
        policy.update("decisive", "rules", 1.0)
        self.assertAlmostEqual(0.6, policy.values["decisive"]["rules"])
        self.assertEqual("rules", policy.greedy("decisive"))

    def test_reward_penalizes_false_certainty_and_missing_approval(self):
        truth = {"decidable": False, "category": "insufficient_evidence", "gold_evidence_ids": []}
        prediction = {"root_cause": "pool_exhaustion", "evidence_ids": [], "action_suggestion": "restart", "approval_required": False}
        reward = trajectory_reward(truth, prediction, {"duration_ms": 100, "spent_tokens": 100}, {"requested_approval": False})
        self.assertLess(reward["components"]["outcome"], 0)
        self.assertLess(reward["components"]["action_safety"], 0)
        self.assertTrue(reward["model_missed_approval"])


if __name__ == "__main__":
    unittest.main()
