"""key-provisioner settings (environment only; see README)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from llmops_common.config import ConfigError, env, env_float, env_int, env_list

SINKS = ("aws-secretsmanager", "vault", "file")
_PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def parse_alias_map(raw: str | None) -> dict[str, tuple[str, ...]]:
    """MODEL_ALIAS_MAP: {"claude-sonnet-5-5": "sonnet-tw"} or {"claude-sonnet-5-5": ["a", "b"]}; identity if unset."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("not an object")
        out = {}
        for catalog_id, target in data.items():
            targets = [target] if isinstance(target, str) else list(target)
            if not targets or not all(isinstance(t, str) and t for t in targets):
                raise TypeError(f"{catalog_id}: give a gateway alias or a list of aliases")
            out[str(catalog_id)] = tuple(targets)
        return out
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"MODEL_ALIAS_MAP must be a JSON object of catalog id -> gateway alias(es): {exc}") from exc


@dataclass(frozen=True)
class Settings:
    litellm_url: str
    secret_sink: str
    litellm_timeout: float = 30.0
    model_alias_map: dict = field(default_factory=dict)
    default_models: tuple[str, ...] = ()
    alias_prefix: str = "req"
    path_template: str = "llmops/keys/{key_alias}"
    gateway_public_url: str | None = None
    retry_base_seconds: float = 30.0
    health_port: int = 8081
    poll_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "Settings":
        url = env("LITELLM_URL", None) or env("LLM_GATEWAY_URL", None)
        if not url:
            raise ConfigError("missing required setting LITELLM_URL (or LLM_GATEWAY_URL)")
        env("LITELLM_MASTER_KEY")  # required (or LITELLM_MASTER_KEY_FILE); read again on every call
        sink = env("SECRET_SINK")
        if sink not in SINKS:
            raise ConfigError(f"SECRET_SINK must be one of {', '.join(SINKS)}, got {sink!r}")
        prefix = env("KEY_ALIAS_PREFIX", "req")
        if not _PREFIX_RE.match(prefix):
            raise ConfigError("KEY_ALIAS_PREFIX must be lowercase letters, digits and dashes")
        template = env("SECRET_PATH_TEMPLATE", "llmops/keys/{key_alias}")
        if "{key_alias}" not in template and not ("{request_id}" in template and "{stage}" in template):
            raise ConfigError("SECRET_PATH_TEMPLATE must contain {key_alias} (or {request_id} and {stage})")
        return cls(
            litellm_url=url.rstrip("/"),
            secret_sink=sink,
            litellm_timeout=env_float("LITELLM_TIMEOUT", 30.0),
            model_alias_map=parse_alias_map(env("MODEL_ALIAS_MAP", None)),
            default_models=env_list("DEFAULT_MODELS", ()),
            alias_prefix=prefix,
            path_template=template,
            gateway_public_url=env("GATEWAY_PUBLIC_URL", None),
            retry_base_seconds=env_float("RETRY_BASE_SECONDS", 30.0),
            health_port=env_int("HEALTH_PORT", 8081),
            poll_seconds=env_float("WORKER_POLL_SECONDS", 2.0),
        )
