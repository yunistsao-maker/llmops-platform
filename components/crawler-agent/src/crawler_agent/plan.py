"""The daily plan: which roles run, in which order, with which request and budget (no heavy imports: ``--help``
and ``roles`` must work anywhere)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Iterable

_RESEARCH = {"searches": 8, "fetches": 14, "toolCalls": 60}


@dataclass(frozen=True)
class Step:
    role: str
    request: dict


#: The daily plan: role order, request and budget exactly as in the AWS orchestrator.  The portal reviewer runs after
#: the researchers and before the Radar editor, which runs last and writes the day's digest.
DAILY: tuple[Step, ...] = (
    Step("bedrock-researcher", {"kind": "focus", "role": "bedrock-researcher", "budget": dict(_RESEARCH)}),
    Step("azure-researcher", {"kind": "focus", "role": "azure-researcher", "budget": dict(_RESEARCH)}),
    Step("gcp-researcher", {"kind": "focus", "role": "gcp-researcher", "budget": dict(_RESEARCH)}),
    Step("vendor-api-researcher", {"kind": "focus", "role": "vendor-api-researcher", "budget": dict(_RESEARCH)}),
    Step("litellm-researcher", {"kind": "focus", "role": "litellm-researcher", "budget": dict(_RESEARCH)}),
    Step("open-model-scout", {"kind": "focus", "role": "open-model-scout", "budget": {"searches": 10, "fetches": 18, "toolCalls": 80}}),
    Step("portal-reviewer", {"kind": "focus", "role": "portal-reviewer", "budget": {"searches": 8, "fetches": 14, "toolCalls": 70}}),
    Step("radar-editor", {"kind": "weekly", "role": "radar-editor", "includeDigest": True,
                          "budget": {"searches": 8, "fetches": 14, "toolCalls": 70}}),
)
ROLE_ORDER: tuple[str, ...] = tuple(s.role for s in DAILY)


def plan(roles: Iterable[str] | None = None) -> list[Step]:
    """The steps for ``roles`` (all when None), always in the daily order."""
    if roles is None:
        return [Step(s.role, copy.deepcopy(s.request)) for s in DAILY]
    wanted = [r.strip() for r in roles if r and r.strip()]
    unknown = [r for r in wanted if r not in ROLE_ORDER]
    if unknown:
        raise ValueError(f"unknown role(s) {unknown}; known: {', '.join(ROLE_ORDER)}")
    if not wanted:
        raise ValueError("no role given")
    return [Step(s.role, copy.deepcopy(s.request)) for s in DAILY if s.role in wanted]


def step_for(role: str, overrides: dict | None = None) -> Step:
    """The daily request of one role, optionally changed by ``overrides`` (any agent request keys; the role stays)."""
    base = next((s for s in DAILY if s.role == role), None)
    if base is None:
        raise ValueError(f"unknown role {role!r}; known: {', '.join(ROLE_ORDER)}")
    request = copy.deepcopy(base.request)
    if overrides:
        if not isinstance(overrides, dict):
            raise ValueError("--request must be a JSON object")
        if "role" in overrides and overrides["role"] != role:
            raise ValueError("--request cannot name another role")
        request.update(overrides)
    return Step(role, request)
