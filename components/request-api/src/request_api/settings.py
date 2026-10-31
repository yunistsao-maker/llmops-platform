"""Settings for request-api, read once from the environment (see README for every variable)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from llmops_common.config import ConfigError, env, env_bool, env_float, env_int

DURATION_RE = re.compile(r"^[1-9][0-9]{0,4}(s|m|h|d|w|mo)$")   # LiteLLM duration units


def _duration(name: str, value: str | None) -> str | None:
    if value in (None, "", "none", "never"):
        return None
    if not DURATION_RE.match(value):
        raise ConfigError(f"{name} must look like 30d, 12h or 1mo (LiteLLM duration), got {value!r}")
    return value


def _model_prices(raw: str | None) -> dict[str, tuple[float, float]]:
    """BUDGET_MODEL_PRICES: {"claude-opus-5-5": {"in": 15, "out": 75}} or {"claude-opus-5-5": [15, 75]}."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out = {}
        for model, price in data.items():
            if isinstance(price, dict):
                out[str(model)] = (float(price["in"]), float(price["out"]))
            else:
                p_in, p_out = price
                out[str(model)] = (float(p_in), float(p_out))
        return out
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ConfigError(f"BUDGET_MODEL_PRICES must map model ids to {{\"in\": x, \"out\": y}} (USD per 1M tokens): {exc}") from exc


@dataclass(frozen=True)
class LimitSettings:
    """Inputs of the budget / rate-limit formula (documented in the README)."""
    peak_factor: float = 10.0          # peak minute traffic = PEAK_FACTOR x the average minute
    rpm_min: int = 10
    rpm_max: int = 6000
    tpm_min: int = 10_000
    tpm_max: int = 10_000_000
    default_rpd: int = 1000            # used when the form says 0 requests a day (unknown)
    default_tokens_in: int = 2000
    default_tokens_out: int = 500
    price_in: float = 3.0              # USD per 1M input tokens when a model has no configured price
    price_out: float = 15.0            # USD per 1M output tokens
    model_prices: dict = field(default_factory=dict)
    headroom: float = 1.5
    budget_min: float = 20.0
    budget_max: float = 20_000.0
    trial_days: int = 14
    full_budget_days: int = 30
    full_key_duration: str | None = None   # None = the full key does not expire

    @classmethod
    def from_env(cls) -> "LimitSettings":
        s = cls(
            peak_factor=env_float("LIMIT_PEAK_FACTOR", 10.0),
            rpm_min=env_int("LIMIT_RPM_MIN", 10),
            rpm_max=env_int("LIMIT_RPM_MAX", 6000),
            tpm_min=env_int("LIMIT_TPM_MIN", 10_000),
            tpm_max=env_int("LIMIT_TPM_MAX", 10_000_000),
            default_rpd=env_int("LIMIT_DEFAULT_RPD", 1000),
            default_tokens_in=env_int("LIMIT_DEFAULT_TOKENS_IN", 2000),
            default_tokens_out=env_int("LIMIT_DEFAULT_TOKENS_OUT", 500),
            price_in=env_float("BUDGET_PRICE_IN_PER_MTOK", 3.0),
            price_out=env_float("BUDGET_PRICE_OUT_PER_MTOK", 15.0),
            model_prices=_model_prices(env("BUDGET_MODEL_PRICES", None)),
            headroom=env_float("BUDGET_HEADROOM", 1.5),
            budget_min=env_float("BUDGET_MIN_USD", 20.0),
            budget_max=env_float("BUDGET_MAX_USD", 20_000.0),
            trial_days=env_int("TRIAL_DAYS", 14),
            full_budget_days=env_int("FULL_BUDGET_PERIOD_DAYS", 30),
            full_key_duration=_duration("FULL_KEY_DURATION", env("FULL_KEY_DURATION", None)),
        )
        if s.rpm_min > s.rpm_max or s.tpm_min > s.tpm_max or s.budget_min > s.budget_max:
            raise ConfigError("LIMIT_*_MIN / BUDGET_MIN_USD must not exceed the matching maximum")
        if s.trial_days < 1 or s.full_budget_days < 1 or s.peak_factor <= 0 or s.headroom <= 0:
            raise ConfigError("TRIAL_DAYS, FULL_BUDGET_PERIOD_DAYS, LIMIT_PEAK_FACTOR and BUDGET_HEADROOM must be positive")
        return s


def default_schema_path() -> Path:
    """REQUEST_SCHEMA_PATH, else the copy baked into the image, else the repo's contracts/ (tests, local runs)."""
    configured = env("REQUEST_SCHEMA_PATH", None)
    if configured:
        return Path(configured)
    baked = Path("/app/schemas/request.schema.json")
    if baked.is_file():
        return baked
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "schemas" / "request.schema.json"
        if candidate.is_file():
            return candidate
    return baked


@dataclass(frozen=True)
class Settings:
    schema_path: Path
    run_worker: bool = True
    worker_poll_seconds: float = 2.0
    allow_self_approval: bool = False
    max_open_per_user: int = 20
    max_subscriptions_per_user: int = 50
    max_body_bytes: int = 64 * 1024
    page_default: int = 50
    page_max: int = 200
    limits: LimitSettings = field(default_factory=LimitSettings)

    @classmethod
    def from_env(cls) -> "Settings":
        dev = (env("AUTH_MODE", "oidc") or "").lower() == "dev"
        s = cls(
            schema_path=default_schema_path(),
            run_worker=env_bool("REQUEST_API_WORKER", True),
            worker_poll_seconds=env_float("WORKER_POLL_SECONDS", 2.0),
            allow_self_approval=env_bool("ALLOW_SELF_APPROVAL", dev),
            max_open_per_user=env_int("MAX_OPEN_REQUESTS_PER_USER", 20),
            max_subscriptions_per_user=env_int("MAX_SUBSCRIPTIONS_PER_USER", 50),
            max_body_bytes=env_int("MAX_BODY_BYTES", 64 * 1024),
            page_default=env_int("PAGE_SIZE_DEFAULT", 50),
            page_max=env_int("PAGE_SIZE_MAX", 200),
            limits=LimitSettings.from_env(),
        )
        if not (1 <= s.page_default <= s.page_max):
            raise ConfigError("PAGE_SIZE_DEFAULT must be between 1 and PAGE_SIZE_MAX")
        return s
