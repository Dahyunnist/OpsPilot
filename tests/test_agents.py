import unittest
from unittest.mock import patch

from opspilot.agents import investigate_multi_agent
from opspilot.a2a import A2AClient, A2AServer, TaskStore
from opspilot.agent_protocol import AgentMessage, EvidenceRef
from opspilot.cli import load_incidents
from opspilot.investigator import investigate


class FakeModel:
    model = "test-model"
    timeout = 1

    def complete(self, system, user, token_budget):
        if "adjudicator" in system:
            data = {"status": "determined", "root_cause": "database pool", "confidence": 0.7,
                    "reasoning": "evidence supports this", "evidence_ids": ["pool-01-m1"],
                    "rejected_hypotheses": [], "uncertainty": "limited sample",
                    "action_suggestion": "restart_order", "approval_required": False}
        elif "cross-examiner" in system:
            data = {"challenges": [], "conflict_summary": "no direct conflict", "handoff": "judge"}
        else:
            data = {"hypotheses": [{"id": "pool_exhaustion", "claim": "pool pressure", "confidence": 0.7,
                                    "evidence_ids": ["pool-01-m1"], "reasoning": "high usage"}],
                    "contradictions": [], "missing_data": [], "handoff": "cross_examiner"}
        return {"data": data, "usage": {"total_tokens": 20}, "duration_ms": 1}


class AgentTests(unittest.TestCase):
    def test_message_is_typed_and_serializable(self):
        message = AgentMessage("t", "m", "logs_expert", "cross_examiner", "handoff", {}, (EvidenceRef("logs", "l1"),), budget_tokens=100)
        self.assertEqual("l1", message.to_dict()["evidence"][0]["record_id"])

    def test_independent_agents_handoff_and_adjudicate_with_validated_refs(self):
        incidents = load_incidents(__import__("pathlib").Path("data/incidents.jsonl"))
        baseline = investigate(incidents["pool-01"])
        with patch("opspilot.agents.ChatModel", FakeModel):
            report = investigate_multi_agent(incidents["pool-01"], baseline, token_budget=1200)
        self.assertEqual(4, len(report["messages"]))
        self.assertEqual({"metrics_expert": ["query_metrics"], "logs_expert": ["query_logs"], "trace_deployment_expert": ["query_traces"], "deployment_expert": ["query_deployments"]}, {item["sender"]: item["allowed_tools"] for item in report["messages"]})
        self.assertEqual(6, report["model"]["calls"])
        self.assertTrue(report["adjudication"]["citation_validation"]["all_references_exist"])
        self.assertTrue(report["adjudication"]["approval_required"])
        self.assertEqual(0, report["trace"]["failures"])
        self.assertTrue(any(event["event"] == "a2a_protocol" for event in report["trace"]["events"]))

    def test_a2a_card_message_task_poll_and_message_idempotency(self):
        def slow_handler(payload):
            import time
            time.sleep(0.05)
            return {"conflict_summary": "checked", "payload": payload}

        with A2AServer(slow_handler) as server:
            client = A2AClient(server.card_url, timeout=2, poll_interval=0.02)
            first, trace = client.send_and_wait("parent-1", {"findings": []}, "msg-fixed")
            second, second_trace = client.send_and_wait("parent-1", {"findings": []}, "msg-fixed")
        self.assertEqual("0.3.0", client.card["protocolVersion"])
        self.assertEqual(["application/json"], client.card["defaultInputModes"])
        self.assertEqual("checked", first["conflict_summary"])
        self.assertEqual(first, second)
        self.assertEqual("message/send", trace[0]["method"])
        self.assertTrue(any(step["method"] == "tasks/get" for step in trace))
        self.assertEqual(1, len(second_trace))

    def test_a2a_task_cancel_is_terminal(self):
        store = TaskStore()
        task, created = store.create("m", {"task_id": "parent"})
        self.assertTrue(created)
        canceled = store.cancel(task["id"])
        self.assertEqual("canceled", canceled["status"]["state"])
        duplicate, created_again = store.create("m", {"task_id": "parent"})
        self.assertFalse(created_again)
        self.assertEqual(task["id"], duplicate["id"])


if __name__ == "__main__":
    unittest.main()
