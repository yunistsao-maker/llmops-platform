"""Model factory.  ``MODEL_PROVIDER`` = gateway (default) | scripted (offline test double).

Components never call a cloud model directly: the agent talks to the platform's LLM gateway (LiteLLM, OpenAI-compatible)
with Strands' OpenAI provider.  ``LLM_MODEL`` is the gateway's model alias (default ``agent-research``, which the gateway
maps to the research model - Claude Sonnet on Bedrock when this agent ran on AWS); ``LLM_GATEWAY_KEY`` is this
component's virtual key.

Claude on the gateway: do NOT set temperature / top_p (non-default values return 400) and do not force tool_choice (400) -
the agent never needs either, and nothing here sends them.
"""

from __future__ import annotations

import os
from typing import Any

from research.config import Settings

USER_AGENT = "llmops-crawler-agent/0.1"


def openai_base_url(gateway_url: str) -> str:
    """The OpenAI-style base URL of the gateway: ``http://llm-gateway:4000`` -> ``http://llm-gateway:4000/v1``."""
    url = (gateway_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("LLM_GATEWAY_URL is not set (e.g. http://llm-gateway:4000)")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"LLM_GATEWAY_URL must start with http:// or https://, got {url[:60]!r}")
    return url if url.endswith("/v1") else url + "/v1"


def gateway_client_args(settings: Settings, **extra: Any) -> dict:
    """Arguments for ``openai.AsyncOpenAI``.  The OpenAI client honours HTTPS_PROXY / HTTP_PROXY / NO_PROXY."""
    return {
        "base_url": openai_base_url(settings.gateway_url),
        # a gateway without keys (local development) still needs a non-empty value for the client
        "api_key": settings.gateway_key or "no-key",
        "timeout": settings.llm_timeout,
        "max_retries": 3,  # 408 / 409 / 429 / 5xx and connection errors, with backoff
        "default_headers": {"User-Agent": USER_AGENT},
        **extra,
    }


def gateway_params(settings: Settings) -> dict:
    params: dict[str, Any] = {"max_tokens": settings.max_tokens}
    if settings.model_extra_fields:
        params["extra_body"] = dict(settings.model_extra_fields)  # e.g. a reasoning setting the gateway passes on
    return params


def gateway_model(settings: Settings, *, client: Any = None):
    """Strands' OpenAI-compatible provider pointed at the gateway.

    ``client``: a ready ``openai.AsyncOpenAI`` (tests inject one over ``httpx.MockTransport``).  Without it Strands makes
    a fresh client per model call from ``gateway_client_args`` (an httpx client cannot be shared across event loops).
    """
    from strands.models.openai import OpenAIModel

    if client is not None:
        return OpenAIModel(client=client, model_id=settings.model_id, params=gateway_params(settings))
    return OpenAIModel(client_args=gateway_client_args(settings), model_id=settings.model_id, params=gateway_params(settings))


def load_model(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    provider = settings.model_provider

    if provider == "scripted":
        from research.offline.scripted_model import ScriptedModel, load_script_file, smoke_script

        path = os.environ.get("SCRIPT_FILE")
        return ScriptedModel(load_script_file(path) if path else smoke_script())

    if provider != "gateway":
        raise ValueError(f"unknown MODEL_PROVIDER {provider!r} (gateway | scripted)")
    return gateway_model(settings)
