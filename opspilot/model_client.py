"""Minimal OpenAI-compatible chat client with strict timeout and response bounds."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_project_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.search(r"(?i)(authorization|api.?key|password|secret|token|email|phone)", key) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
        value = re.sub(r"(?<!\d)(?:\+?\d[\s-]?){8,15}(?!\d)", "[REDACTED_PHONE]", value)
    return value


class ModelError(RuntimeError):
    def __init__(self, message: str, *, category: str = "unknown", code: str | int | None = None,
                 retryable: bool = False, finish_reason: str | None = None,
                 completion_tokens: int | None = None, completion_token_budget: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.retryable = retryable
        self.finish_reason = finish_reason
        self.completion_tokens = completion_tokens
        self.completion_token_budget = completion_token_budget

    def diagnostic(self) -> dict[str, Any]:
        trace = {"category": self.category, "code": self.code, "retryable": self.retryable}
        if self.category == "provider_http" and isinstance(self.code, int):
            trace["http_status"] = self.code
        if self.finish_reason is not None:
            trace["finish_reason"] = self.finish_reason[:40]
        if self.completion_tokens is not None:
            trace["completion_tokens"] = max(0, self.completion_tokens)
        if self.completion_token_budget is not None:
            trace["completion_token_budget"] = max(0, self.completion_token_budget)
        return trace


class ChatModel:
    def __init__(self) -> None:
        load_project_env()
        self.base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "")
        self.timeout = min(120.0, max(2.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))))
        self.max_tokens = min(4096, max(64, int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "512"))))
        if not (self.base_url and self.api_key and self.model):
            raise ModelError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set in .env",
                category="configuration", code="missing_credentials")

    def complete(self, system: str, user: str, token_budget: int) -> dict[str, Any]:
        cap = min(self.max_tokens, max(64, token_budget))
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "max_tokens": cap,
            **({"reasoning_effort": "none"} if self.model.startswith("deepseek-v4") else {}),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(1_000_000))
        except urllib.error.HTTPError as exc:
            raise ModelError(f"model request failed: HTTP {exc.code}", category="provider_http",
                code=exc.code, retryable=exc.code in {408, 425, 429} or exc.code >= 500) from exc
        except TimeoutError as exc:
            raise ModelError("model request timed out", category="transport_timeout", retryable=True) from exc
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, TimeoutError)
            raise ModelError("model request timed out" if timed_out else "model transport failed",
                category="transport_timeout" if timed_out else "transport_error", retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise ModelError("model returned invalid response JSON", category="response_schema",
                code="invalid_response_json") from exc
        try:
            choice = result["choices"][0]
            content = choice["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            choices = result.get("choices", []) if isinstance(result, dict) else []
            first_choice = choices[0] if isinstance(choices, list) and choices else {}
            finish = first_choice.get("finish_reason", "unknown") if isinstance(first_choice, dict) else "unknown"
            usage = result.get("usage", {}) if isinstance(result, dict) else {}
            completion_tokens = usage.get("completion_tokens", "unknown") if isinstance(usage, dict) else "unknown"
            truncated = finish == "length"
            raise ModelError(f"model returned invalid JSON response (finish={finish}, completion_tokens={completion_tokens})",
                category="response_schema", code="truncated_json" if truncated else "invalid_json_content",
                retryable=truncated, finish_reason=str(finish),
                completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
                completion_token_budget=cap) from exc
        if not isinstance(parsed, dict):
            raise ModelError("model returned JSON that was not an object", category="response_schema",
                code="expected_object")
        usage = result.get("usage", {})
        return {"data": parsed, "usage": usage, "duration_ms": round((time.monotonic() - started) * 1000, 1)}
