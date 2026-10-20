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
    pass


class ChatModel:
    def __init__(self) -> None:
        load_project_env()
        self.base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "")
        self.timeout = min(120.0, max(2.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))))
        self.max_tokens = min(4096, max(64, int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "512"))))
        if not (self.base_url and self.api_key and self.model):
            raise ModelError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set in .env")

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
            raise ModelError(f"model request failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelError(f"model request failed: {type(exc).__name__}") from exc
        try:
            choice = result["choices"][0]
            content = choice["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            finish = result.get("choices", [{}])[0].get("finish_reason", "unknown") if isinstance(result, dict) else "unknown"
            usage = result.get("usage", {}) if isinstance(result, dict) else {}
            raise ModelError(f"model returned invalid JSON response (finish={finish}, completion_tokens={usage.get('completion_tokens', 'unknown')})") from exc
        usage = result.get("usage", {})
        return {"data": parsed, "usage": usage, "duration_ms": round((time.monotonic() - started) * 1000, 1)}
