from __future__ import annotations

import io
import json
import os
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from opspilot.model_client import ChatModel, ModelError


class Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _size): return json.dumps(self.payload).encode()


class ModelClientErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "LLM_BASE_URL": "http://127.0.0.1:9000/v1", "LLM_API_KEY": "unit-test-secret",
            "LLM_MODEL": "deepseek-v4-flash", "LLM_TIMEOUT_SECONDS": "5",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.model = ChatModel()

    def test_provider_http_error_trace_keeps_status_without_response_body(self) -> None:
        body = b"provider-secret-response-body"
        failure = HTTPError("http://127.0.0.1:9000/v1/chat/completions", 503, "unavailable", {}, io.BytesIO(body))
        self.addCleanup(failure.close)
        with patch("opspilot.model_client.urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(ModelError) as caught:
                self.model.complete("system", "user", 128)
        diagnostic = caught.exception.diagnostic()
        self.assertEqual(diagnostic["category"], "provider_http")
        self.assertEqual(diagnostic["code"], 503)
        self.assertTrue(diagnostic["retryable"])
        self.assertNotIn(body.decode(), json.dumps(diagnostic))
        self.assertNotIn("unit-test-secret", json.dumps(diagnostic))

    def test_timeout_and_url_timeout_have_distinct_sanitized_classification(self) -> None:
        for failure in (TimeoutError("timeout-secret"), URLError(TimeoutError("timeout-secret"))):
            with self.subTest(failure=type(failure).__name__):
                with patch("opspilot.model_client.urllib.request.urlopen", side_effect=failure):
                    with self.assertRaises(ModelError) as caught:
                        self.model.complete("system", "user", 128)
                diagnostic = caught.exception.diagnostic()
                self.assertEqual(diagnostic["category"], "transport_timeout")
                self.assertTrue(diagnostic["retryable"])
                self.assertNotIn("timeout-secret", json.dumps(diagnostic))

    def test_invalid_schema_trace_keeps_finish_metadata_not_content(self) -> None:
        payload = {"choices": [{"message": {"content": "sensitive-non-json-content"},
            "finish_reason": "length"}], "usage": {"completion_tokens": 64}}
        with patch("opspilot.model_client.urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(ModelError) as caught:
                self.model.complete("system", "user", 128)
        diagnostic = caught.exception.diagnostic()
        self.assertEqual(diagnostic["category"], "response_schema")
        self.assertEqual(diagnostic["code"], "invalid_json_content")
        self.assertEqual(diagnostic["finish_reason"], "length")
        self.assertEqual(diagnostic["completion_tokens"], 64)
        self.assertNotIn("sensitive-non-json-content", json.dumps(diagnostic))

    def test_non_object_json_is_classified_without_retaining_provider_content(self) -> None:
        payload = {"choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}], "usage": {}}
        with patch("opspilot.model_client.urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(ModelError) as caught:
                self.model.complete("system", "user", 128)
        diagnostic = caught.exception.diagnostic()
        self.assertEqual(diagnostic["category"], "response_schema")
        self.assertEqual(diagnostic["code"], "expected_object")
        self.assertNotIn("[]", json.dumps(diagnostic))


if __name__ == "__main__":
    unittest.main()
