"""Runtime settings, read from environment variables (docs/runtime-contract.md: configuration is environment only).

On the platform the agent is a plain CLI process (``python -m crawler_agent``), not an AgentCore runtime:

* the model is reached through the LLM gateway (OpenAI-compatible): ``LLM_GATEWAY_URL``, ``LLM_GATEWAY_KEY`` (or
  ``LLM_GATEWAY_KEY_FILE``), ``LLM_MODEL`` (default alias ``agent-research``);
* portal data and results live in the curated bucket of any S3-compatible store (``research/objectstore.py``):
  ``PORTAL_DATA_URI`` defaults to ``s3://<BUCKET_CURATED>/portal/``, ``OUTPUT_URI`` to ``s3://<BUCKET_CURATED>/research/``;
* web search is ``SEARCH_BACKEND`` (``none`` = the official-pages fallback, research/web/search_backend.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_MODEL_ALIAS = "agent-research"
#: a single agent turn can write up to MAX_TOKENS of output: the gateway call needs far more than the platform's 60 s default
DEFAULT_LLM_TIMEOUT = 600.0


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(x.strip().lower() for x in value.split(",") if x.strip())


def _secret(e: Mapping[str, str], name: str) -> str:
    """NAME, or the content of the file NAME_FILE points to (a mounted Secret).  "" when neither is set."""
    value = e.get(name) or ""
    if not value and e.get(name + "_FILE"):
        with open(e[name + "_FILE"], encoding="utf-8") as fh:
            value = fh.read().strip()
    return value


def curated_bucket(e: Mapping[str, str]) -> str:
    """The curated bucket's name (``BUCKET_CURATED``, default ``llmops-curated``: llmops_common.storage.bucket)."""
    return (e.get("BUCKET_CURATED") or "").strip() or "llmops-curated"


@dataclass(frozen=True)
class Settings:
    # --- model (through the LLM gateway) ---------------------------------------
    model_provider: str = "gateway"  # gateway | scripted (offline test double)
    model_id: str = DEFAULT_MODEL_ALIAS  # the gateway's model alias (LLM_MODEL)
    gateway_url: str = ""  # LLM_GATEWAY_URL, e.g. http://llm-gateway:4000
    gateway_key: str = ""  # LLM_GATEWAY_KEY: this component's virtual key (never logged)
    llm_timeout: float = DEFAULT_LLM_TIMEOUT
    max_tokens: int = 32000
    model_extra_fields: Mapping[str, Any] | None = None  # extra request body fields for the gateway (MODEL_EXTRA_FIELDS, JSON)
    # --- portal data (read-only) ---------------------------------------------
    portal_data: str = "bundled"  # bundled | /path/to/portal-repo | s3://bucket/prefix/
    # --- web search -------------------------------------------------------------
    search_backend: str = "none"  # none = official-pages fallback only; other names: research.web.search_backend.PROVIDERS
    # --- fetching -----------------------------------------------------------
    fetch_timeout: float = 20.0
    fetch_max_bytes: int = 3_000_000
    extra_allow_domains: tuple[str, ...] = ()
    extra_official_domains: tuple[str, ...] = ()  # hosts the operator vouches for as a vendor's own documentation
    # --- budgets (per invocation) ---------------------------------------------
    max_searches: int = 30
    max_fetches: int = 40
    max_tool_calls: int = 200
    max_turns: int = 120  # model calls per run (Strands loop limit)
    max_total_tokens: int = 6_000_000  # input+output tokens summed over the run (spend guard)
    max_runtime_seconds: int = 1500
    # --- output -------------------------------------------------------------
    output_uri: str = ""  # s3://bucket/prefix/ or a directory: where results are saved ("" = only returned)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        extra = e.get("MODEL_EXTRA_FIELDS")
        extra_fields = None
        if extra:
            try:
                parsed = json.loads(extra)
                extra_fields = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                extra_fields = None
        bucket = curated_bucket(e)
        return cls(
            model_provider=(e.get("MODEL_PROVIDER") or "gateway").strip().lower(),
            model_id=(e.get("LLM_MODEL") or "").strip() or DEFAULT_MODEL_ALIAS,
            gateway_url=(e.get("LLM_GATEWAY_URL") or "").strip(),
            gateway_key=_secret(e, "LLM_GATEWAY_KEY"),
            llm_timeout=_float(e.get("LLM_TIMEOUT"), DEFAULT_LLM_TIMEOUT),
            max_tokens=_int(e.get("MAX_TOKENS"), 32000),
            model_extra_fields=extra_fields,
            portal_data=e.get("PORTAL_DATA_URI") or f"s3://{bucket}/portal/",
            search_backend=(e.get("SEARCH_BACKEND") or "none").strip().lower(),
            fetch_timeout=_float(e.get("FETCH_TIMEOUT"), 20.0),
            fetch_max_bytes=_int(e.get("FETCH_MAX_BYTES"), 3_000_000),
            extra_allow_domains=_csv(e.get("EXTRA_ALLOW_DOMAINS")),
            extra_official_domains=_csv(e.get("EXTRA_OFFICIAL_DOMAINS")),
            max_searches=_int(e.get("MAX_SEARCHES"), 30),
            max_fetches=_int(e.get("MAX_FETCHES"), 40),
            max_tool_calls=_int(e.get("MAX_TOOL_CALLS"), 200),
            max_turns=_int(e.get("MAX_TURNS"), 120),
            max_total_tokens=_int(e.get("MAX_TOTAL_TOKENS"), 6_000_000),
            max_runtime_seconds=_int(e.get("MAX_RUNTIME_SECONDS"), 1500),
            output_uri=e.get("OUTPUT_URI") or f"s3://{bucket}/research/",
        )

    def public(self) -> dict:
        """What may be logged: everything but the gateway key."""
        from dataclasses import asdict

        out = asdict(self)
        out["gateway_key"] = "***" if self.gateway_key else ""
        return out
