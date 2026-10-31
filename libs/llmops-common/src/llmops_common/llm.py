"""Client for the LLM gateway (LiteLLM, OpenAI-compatible). Components never call a cloud model directly.

Settings:
    LLM_GATEWAY_URL   e.g. http://llm-gateway:4000
    LLM_GATEWAY_KEY   this component's virtual key (or LLM_GATEWAY_KEY_FILE)
    LLM_MODEL         default model alias for this component (portal-quick, agent-research, judge, ...)
    LLM_TIMEOUT       seconds, default 60
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from .config import env, env_float

__all__ = ["GatewayError", "ChatResult", "GatewayClient", "extract_json"]


class GatewayError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class ChatResult:
    text: str
    model: str | None = None            # the model that actually answered (SEC-11 records this)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict, repr=False)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str):
    """Parse a JSON object from model text, tolerating code fences and leading prose."""
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            return json.loads(t[start:end + 1])
        raise


class GatewayClient:
    def __init__(self, base_url: str, api_key: str | None, default_model: str | None = None,
                 timeout: float = 60.0, transport: httpx.BaseTransport | None = None, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.max_retries = max_retries
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    @classmethod
    def from_env(cls, model_env: str = "LLM_MODEL") -> "GatewayClient":
        return cls(env("LLM_GATEWAY_URL"), env("LLM_GATEWAY_KEY", None), env(model_env, None),
                   timeout=env_float("LLM_TIMEOUT", 60.0))

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: dict) -> dict:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.post(path, json=body)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise GatewayError(f"gateway unreachable: {type(exc).__name__}") from exc
            else:
                if resp.status_code < 400:
                    return resp.json()
                retryable = resp.status_code in (408, 429, 500, 502, 503, 504)
                if not retryable or attempt >= self.max_retries:
                    code = None
                    try:
                        err = resp.json().get("error", {})
                        code = err.get("code") or err.get("type")
                        message = err.get("message") or resp.text
                    except ValueError:
                        message = resp.text
                    raise GatewayError(f"gateway {resp.status_code}: {str(message)[:300]}", resp.status_code, code)
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
        raise GatewayError("unreachable")  # pragma: no cover

    def chat(self, messages: list[dict], *, model: str | None = None, max_tokens: int | None = None,
             temperature: float | None = None, response_format: dict | None = None, extra: dict | None = None) -> ChatResult:
        body: dict = {"model": model or self.default_model, "messages": messages}
        if not body["model"]:
            raise GatewayError("no model alias given (set LLM_MODEL)")
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None:
            body["response_format"] = response_format
        if extra:
            body.update(extra)
        data = self._post("/v1/chat/completions", body)
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return ChatResult(text=content, model=data.get("model"), finish_reason=choice.get("finish_reason"),
                          usage=data.get("usage") or {}, raw=data)

    def chat_json(self, messages: list[dict], *, model: str | None = None, max_tokens: int | None = None) -> dict:
        """Ask for a JSON object; one repair round if the first answer does not parse."""
        result = self.chat(messages, model=model, max_tokens=max_tokens, response_format={"type": "json_object"})
        try:
            return extract_json(result.text)
        except (json.JSONDecodeError, ValueError):
            repair = messages + [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": "Reply again with only one valid JSON object, no prose."},
            ]
            again = self.chat(repair, model=model, max_tokens=max_tokens, response_format={"type": "json_object"})
            try:
                return extract_json(again.text)
            except (json.JSONDecodeError, ValueError) as exc:
                raise GatewayError("model did not return valid JSON", code="invalid_json") from exc

    def gateway_version(self) -> str | None:
        """LiteLLM's version. v1.105 reports it only on the authenticated /health/readiness/details
        (older releases put it on /health/readiness). None if unavailable."""
        for path in ("/health/readiness/details", "/health/readiness"):
            try:
                resp = self._http.get(path, timeout=10)
                if resp.status_code < 400:
                    version = resp.json().get("litellm_version")
                    if version:
                        return str(version)
            except (httpx.HTTPError, ValueError, AttributeError):
                continue
        return None
