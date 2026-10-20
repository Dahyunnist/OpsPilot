import unittest
import time
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen as actual_urlopen

from opspilot.agents import _evidence_context, _ground_adjudication, investigate_multi_agent
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
                    "reasoning": "evidence supports this", "evidence_ids": ["pool-01-m1", "pool-01-t1"],
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

    def test_cross_exam_context_preserves_trace_joins_without_log_body(self):
        context = _evidence_context({"source": "traces", "records": [{
            "record_id": "t1", "timestamp": "2027-02-01T09:05:00Z", "service": "gateway",
            "message": "sensitive raw body", "attributes": {"trace_id": "trace-1", "service.version": "v3",
                "span": "gateway.request", "peer.service": "order", "status": "error"},
        }]}, {"t1"})
        self.assertEqual("trace-1", context[0]["trace_id"])
        self.assertEqual("order", context[0]["peer_service"])
        self.assertNotIn("message", context[0])

    def test_independent_agents_handoff_and_adjudicate_with_validated_refs(self):
        incidents = load_incidents(__import__("pathlib").Path("data/incidents.jsonl"))
        baseline = investigate(incidents["pool-01"])
        with patch("opspilot.agents.ChatModel", FakeModel):
            report = investigate_multi_agent(incidents["pool-01"], baseline, token_budget=1200)
        self.assertEqual(4, len(report["messages"]))
        self.assertEqual({"metrics_expert": ["query_metrics"], "logs_expert": ["query_logs"], "trace_deployment_expert": ["query_traces"], "deployment_expert": ["query_deployments"]}, {item["sender"]: item["allowed_tools"] for item in report["messages"]})
        self.assertEqual(6, report["model"]["calls"])
        self.assertTrue(report["adjudication"]["citation_validation"]["all_references_exist"])
        self.assertTrue(report["adjudication"]["citation_validation"]["all_references_support_hypothesis"])
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

    def test_a2a_transport_retry_reuses_idempotent_message(self):
        with A2AServer(lambda _payload: {"conflict_summary": "recovered"}) as server:
            client = A2AClient(server.card_url, timeout=2, poll_interval=0.02)
            original = actual_urlopen
            calls = 0

            def transient_failure(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise URLError("injected connection reset")
                return original(*args, **kwargs)

            with patch("opspilot.a2a.urlopen", side_effect=transient_failure):
                result, _trace = client.send_and_wait("parent", {}, "retry-once")
            duplicate, _trace = client.send_and_wait("parent", {}, "retry-once")
        self.assertEqual("recovered", result["conflict_summary"])
        self.assertEqual(result, duplicate)
        self.assertEqual(1, client.retry_count)

    def test_a2a_rejects_non_object_params(self):
        with A2AServer(lambda _payload: {}) as server:
            client = A2AClient(server.card_url, timeout=2, poll_interval=0.02)
            with self.assertRaisesRegex(Exception, "Invalid params"):
                client._rpc("tasks/get", [])
            with self.assertRaisesRegex(Exception, "Invalid params"):
                client._rpc("message/send", {"message": []})

    def test_a2a_deadline_cancels_late_result(self):
        with A2AServer(lambda _payload: (time.sleep(0.35) or {"late": True})) as server:
            client = A2AClient(server.card_url, timeout=0.12, poll_interval=0.02)
            with self.assertRaisesRegex(Exception, "deadline exceeded"):
                client.send_and_wait("parent", {}, "cancel-me")
            task_id = next(iter(server.store.tasks))
            self.assertEqual("canceled", server.store.get(task_id)["status"]["state"])

    def test_live_evidence_gate_withholds_single_source_guess(self):
        incidents = load_incidents(__import__("pathlib").Path("data/incidents.jsonl"))
        baseline = investigate(incidents["unknown-02"])
        result = _ground_adjudication({"status": "probable", "root_cause": "database connection pool exhausted",
                                       "evidence_ids": ["unknown-02-m1"], "uncertainty": "ambiguous"}, baseline)
        self.assertIsNone(result["root_cause"])
        self.assertEqual("database connection pool exhausted", result["proposed_root_cause"])
        self.assertEqual("insufficient_evidence", result["status"])


if __name__ == "__main__":
    unittest.main()
