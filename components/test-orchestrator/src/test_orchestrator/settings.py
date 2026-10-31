"""Settings (environment only; see README for the full table)."""
from __future__ import annotations

from dataclasses import dataclass

from llmops_common.config import env, env_bool, env_float, env_int, env_list


@dataclass
class Settings:
    routes_file: str | None = None
    gateway_name: str = "onprem"
    gw_url: str | None = None
    gw_key: str | None = None
    gw_poll_seconds: float = 300.0
    reap_seconds: float = 120.0
    judge_model: str = "judge"
    max_tokens: int = 2048
    seed: int = 7
    job_max_calls: int = 1500
    runner_max_attempts: int = 3
    smoke_sec_ids: tuple[str, ...] = ("SEC-01", "SEC-04", "SEC-10")
    smoke_n: int = 20
    smoke_trials: int = 1
    route_plan_delay: float = 60.0
    plan_on_route_added: bool = True
    plan_on_gateway_change: bool = True
    sync_routes_on_release: bool = True
    baseline_model_id: str | None = None
    background: bool = True
    poll_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            routes_file=env("ROUTES_FILE", None),
            gateway_name=env("GW_NAME", "onprem"),
            gw_url=env("GW_URL", None),
            gw_key=env("GW_KEY", None),
            gw_poll_seconds=env_float("GW_POLL_SECONDS", 300.0),
            reap_seconds=env_float("REAP_SECONDS", 120.0),
            judge_model=env("JUDGE_MODEL", "judge"),
            max_tokens=env_int("MAX_TOKENS", 2048),
            seed=env_int("SAMPLE_SEED", 7),
            job_max_calls=env_int("JOB_MAX_CALLS", 1500),
            runner_max_attempts=env_int("RUNNER_MAX_ATTEMPTS", 3),
            smoke_sec_ids=env_list("SMOKE_SEC_IDS", ("SEC-01", "SEC-04", "SEC-10")),
            smoke_n=env_int("SMOKE_N", 20),
            smoke_trials=env_int("SMOKE_TRIALS", 1),
            route_plan_delay=env_float("ROUTE_PLAN_DELAY_SECONDS", 60.0),
            plan_on_route_added=env_bool("PLAN_ON_ROUTE_ADDED", True),
            plan_on_gateway_change=env_bool("PLAN_ON_GATEWAY_CHANGE", True),
            sync_routes_on_release=env_bool("SYNC_ROUTES_ON_RELEASE", True),
            baseline_model_id=env("BASELINE_MODEL_ID", None),
            background=env_bool("ORCHESTRATOR_BACKGROUND", True),
            poll_seconds=env_float("POLL_SECONDS", 2.0),
        )
