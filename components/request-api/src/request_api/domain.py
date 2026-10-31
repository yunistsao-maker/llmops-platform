"""Request rules without I/O: gate list, statuses, launch stage and the budget / rate-limit formula.

Gate list stored in requests.gates (jsonb), one entry per required gate, in order:
    [{"gate": "G0", "status": "approved"}, {"gate": "G2", "status": "pending"}, ..., {"gate": "G9", "status": "pending"}]
G0 (intake) is approved on submit. G2/G8 wait for a reviewer. G9 (launch) is approved when the full key is
launched: automatically when no trial is needed, by a G9 decision after the trial otherwise.

Request status:
    in_review -> changes_requested -> (resubmit) in_review
    in_review -> rejected | withdrawn
    in_review -> provisioning (every required review gate approved) -> trial | active | failed
    trial -> (G9 approve) provisioning -> active;  trial -> (G9 reject) rejected
    failed -> (admin reprovision) provisioning
`approved` exists in the schema but is not used: approval moves straight to `provisioning`.
"""
from __future__ import annotations

import math

from llmops_common import gates as G

from .settings import LimitSettings

REVIEW_GATES = G.REVIEW_GATES
DECIDABLE_GATES = REVIEW_GATES + ("G9",)
GATE_ROLE = {"G2": "reviewer-g2", "G8": "reviewer-g8", "G9": "reviewer-g8"}
DECISIONS = ("approve", "reject", "request_changes")
STATUSES = ("in_review", "changes_requested", "approved", "provisioning", "active", "trial",
            "rejected", "failed", "withdrawn")
OPEN_STATUSES = ("in_review", "changes_requested")

# Request fields as in contracts/schemas/request.schema.json, with the schema defaults.
FIELDS = ("team", "alias", "use_case", "models", "unsure", "data_class", "data_class_other", "cloud", "rpd",
          "tokens_in", "tokens_out", "target_date", "external", "taiwan_only", "note")
DEFAULTS = {"models": [], "unsure": False, "cloud": "any", "rpd": 0, "tokens_in": 0, "tokens_out": 0,
            "external": False, "taiwan_only": False}


def ref(request_id) -> str:
    """Short reference shown to people and used in key aliases: req-1a2b3c4d."""
    return "req-" + str(request_id).replace("-", "")[:8]


# -- gates ----------------------------------------------------------------------------------------------
def build_gates(fields: dict) -> list[dict]:
    return [{"gate": g, "status": "approved" if g == "G0" else "pending"} for g in G.required_gates(fields)]


def gate_ids(gates: list[dict]) -> list[str]:
    return [g["gate"] for g in gates]


def gate_status(gates: list[dict], gate: str) -> str | None:
    for g in gates:
        if g["gate"] == gate:
            return g["status"]
    return None


def with_gate(gates: list[dict], gate: str, status: str) -> list[dict]:
    return [dict(g, status=status) if g["gate"] == gate else dict(g) for g in gates]


def pending_review_gate(gates: list[dict]) -> str | None:
    approved = {g["gate"] for g in gates if g["status"] == "approved"}
    return G.next_gate(gate_ids(gates), approved)


def next_gate(status: str, gates: list[dict]) -> str | None:
    """The gate a person has to act on now (None when nobody has to decide anything)."""
    if status == "in_review":
        return pending_review_gate(gates)
    if status == "trial":
        return "G9"
    return None


def launch(gates: list[dict]) -> tuple[list[dict], str]:
    """Every required review gate is approved: a trial first when G8 is required, else the full launch (G9)."""
    if "G8" in gate_ids(gates) and gate_status(gates, "G9") != "approved":
        return gates, "trial"
    return with_gate(gates, "G9", "approved"), "full"


def stage_of(gates: list[dict]) -> str:
    """Stage being (or last) provisioned: full once G9 is approved, else trial."""
    return "full" if gate_status(gates, "G9") == "approved" else "trial"


# -- limits ---------------------------------------------------------------------------------------------
def _clamp(value, low, high):
    return max(low, min(high, value))


def prices_for(models: list[str], ls: LimitSettings) -> tuple[float, float]:
    """USD per 1M tokens (input, output): the most expensive of the requested models (unknown = default price)."""
    pairs = [ls.model_prices.get(m, (ls.price_in, ls.price_out)) for m in models] or [(ls.price_in, ls.price_out)]
    return max(p[0] for p in pairs), max(p[1] for p in pairs)


def compute_limits(fields: dict, stage: str, ls: LimitSettings) -> dict:
    """Budget and rate limits for a key, from the request's volume estimate.

    rpd  = requests per day (LIMIT_DEFAULT_RPD when 0);  tin/tout = tokens per request (defaults when 0)
    rpm  = clamp(ceil(rpd x LIMIT_PEAK_FACTOR / 1440), LIMIT_RPM_MIN, LIMIT_RPM_MAX)
    tpm  = clamp(rpm x (tin + tout), LIMIT_TPM_MIN, LIMIT_TPM_MAX)
    cost per day = rpd x (tin x price_in + tout x price_out) / 1e6   (USD; price = most expensive requested model)
    budget_usd   = clamp(ceil(cost per day x days x BUDGET_HEADROOM), BUDGET_MIN_USD, BUDGET_MAX_USD)
                   days = TRIAL_DAYS for a trial key, FULL_BUDGET_PERIOD_DAYS (budget resets each period) for full
    """
    rpd = int(fields.get("rpd") or 0) or ls.default_rpd
    tin = int(fields.get("tokens_in") or 0) or ls.default_tokens_in
    tout = int(fields.get("tokens_out") or 0) or ls.default_tokens_out
    rpm = int(_clamp(math.ceil(rpd * ls.peak_factor / 1440), ls.rpm_min, ls.rpm_max))
    tpm = int(_clamp(rpm * (tin + tout), ls.tpm_min, ls.tpm_max))
    p_in, p_out = prices_for(list(fields.get("models") or []), ls)
    per_day = rpd * (tin * p_in + tout * p_out) / 1_000_000
    trial = stage == "trial"
    days = ls.trial_days if trial else ls.full_budget_days
    budget = float(_clamp(math.ceil(per_day * days * ls.headroom), ls.budget_min, ls.budget_max))
    return {
        "budget_usd": budget,
        "rpm": rpm,
        "tpm": tpm,
        "duration": f"{ls.trial_days}d" if trial else ls.full_key_duration,
        "budget_duration": None if trial else f"{ls.full_budget_days}d",
        "basis": {"rpd": rpd, "tokens_in": tin, "tokens_out": tout, "price_in_per_mtok": p_in,
                  "price_out_per_mtok": p_out, "days": days, "headroom": ls.headroom},
    }
