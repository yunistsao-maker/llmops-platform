"""LLM-as-judge through the gateway (JUDGE_GATEWAY_URL / JUDGE_GATEWAY_KEY; alias from the job's judge.model).

scoring.ask_judge() expects judge(prompt) -> {text, refusal, error}. The judge is only used where secsuite used one
(SEC-01 JBB, SEC-02/01 CyberSecEval questions without a literal needle, SEC-09, SEC-13, SEC-15). Without a judge those
cases are "review". JUDGE_GATEWAY_URL=mock gives secsuite's simulated judge, for local runs without any model.
"""
from __future__ import annotations

from llmops_common.config import env, env_float, env_int
from llmops_common.llm import GatewayClient, GatewayError
from llmops_common.log import redact

from .providers import MOCK_COMPLY, gateway_base

_MOCK_COMPLIED = ("[模擬] 以下是你要求的內容", "[模擬] 照做", MOCK_COMPLY)


class GatewayJudge:
    def __init__(self, client: GatewayClient, alias: str, max_tokens: int = 1024):
        self.client, self.alias, self.max_tokens = client, alias, max_tokens

    def __call__(self, prompt: str) -> dict:
        try:
            res = self.client.chat([{"role": "user", "content": prompt}], model=self.alias, max_tokens=self.max_tokens)
        except GatewayError as e:
            return {"text": "", "refusal": False, "model": None, "error": (redact(str(e)) or "")[:300]}
        return {"text": res.text, "refusal": res.finish_reason in ("refusal", "content_filter"), "model": res.model, "error": None}


class MockJudge:
    """secsuite's simulated judge: YES only for the simulator's own placeholder of a compliant answer."""

    model = "mock-judge"

    def __call__(self, prompt: str) -> dict:
        response = prompt.split("<RESPONSE>", 1)[-1]
        return {"text": "YES" if any(m in response for m in _MOCK_COMPLIED) else "NO", "refusal": False,
                "model": self.model, "error": None}


class JudgeFactory:
    def __init__(self, client: GatewayClient | None = None):
        self._client = client
        self.max_tokens = env_int("JUDGE_MAX_TOKENS", 1024)

    def make(self, alias: str | None):
        alias = alias or env("JUDGE_MODEL", "judge")
        url = env("JUDGE_GATEWAY_URL", None)
        if self._client is None and url == "mock":
            return MockJudge()
        if self._client is None:
            if not url:
                return None
            self._client = GatewayClient(gateway_base(url), env("JUDGE_GATEWAY_KEY", None),
                                         timeout=env_float("JUDGE_TIMEOUT", 120.0), max_retries=env_int("JUDGE_MAX_RETRIES", 3))
        return GatewayJudge(self._client, alias, self.max_tokens)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
