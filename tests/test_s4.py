import unittest

from scripts.evaluate import evaluate


class EvaluationTests(unittest.TestCase):
    def test_fixed_set_metrics_and_rerun_are_truthful(self):
        result = evaluate()
        metrics = result["metrics"]
        self.assertEqual(8, result["dataset_cases"])
        self.assertTrue(result["stability_check"]["all_decision_signatures_match"])
        self.assertEqual((6, 6), (metrics["top1_root_cause"]["correct"], metrics["top1_root_cause"]["total"]))
        self.assertEqual((2, 2), (metrics["insufficient_evidence_recall"]["correct_abstentions"], metrics["insufficient_evidence_recall"]["total_insufficient"]))
        self.assertEqual((4, 6), (metrics["logs_only_top1"]["correct"], metrics["logs_only_top1"]["total"]))
        self.assertEqual((6, 6), (metrics["no_refutation_top1"]["correct"], metrics["no_refutation_top1"]["total"]))
        self.assertEqual((32, 32), (metrics["evidence_reference_validity"]["valid"], metrics["evidence_reference_validity"]["total"]))
        self.assertEqual((0, 6), (metrics["incorrect_action_recommendation_rate"]["incorrect"], metrics["incorrect_action_recommendation_rate"]["total_recommendations"]))


if __name__ == "__main__":
    unittest.main()
