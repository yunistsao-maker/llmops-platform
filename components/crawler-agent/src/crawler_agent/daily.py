"""The daily research run: the eight roles one after another, as the AWS orchestrator ran them.

On AWS a Step Functions state machine (infra/daily-orchestrator.yaml in llmops-research-agent) started one pipeline
execution per role, in this order, each with the request and budget below.  Here the same requests run in one process.
The roles still hand notes to each other through the result store (research/handoff.py), and a role that fails does
not stop the others: the run is then reported incomplete (exit code 1).

What changed with the platform: nothing is published between roles.  Every role researches against the release that
is live when it starts, and the publisher replays all of the day's proposals afterwards, in this order
(``research.proposal.ready``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from llmops_common.log import kv

from research.agent import run_stream
from research.config import Settings

from .plan import Step

log = logging.getLogger("crawler_agent.daily")

# ---------------------------------------------------------------------------------------------- one role
@dataclass
class RoleOutcome:
    role: str
    status: str  # accepted | no_proposal | error | cancelled | skipped
    run_id: str | None = None
    proposal_id: str | None = None
    saved: str | None = None  # where the run's proposal (or run record) was written
    error: str | None = None
    elapsed: float = 0.0
    stats: dict = field(default_factory=dict)
    error_saving: bool = False  # the result could not be written: its research is lost

    @property
    def failed(self) -> bool:
        """A role that did not finish its research (the AWS orchestrator's "failed role")."""
        return self.status in ("error", "cancelled") or self.error_saving


def _tool_line(ev: dict) -> dict:
    return {"tool": ev.get("name"), "input": ev.get("input")}


async def run_role(step: Step, settings: Settings, *, model: Any = None) -> RoleOutcome:
    """Run one role; log its progress (tool names and short inputs, never prompts or page text); never raises except
    for cancellation."""
    started = time.monotonic()
    log.info("role started", extra=kv(role=step.role, kind=step.request.get("kind"), budget=step.request.get("budget")))
    final: dict = {}
    async for ev in run_stream(step.request, settings=settings, model=model):
        kind = ev.get("type")
        if kind == "result":
            final = ev
        elif kind == "run_started":
            log.info("run started", extra=kv(role=step.role, run_id=ev.get("runId"), model=ev.get("model"), search=ev.get("search"),
                                             portal=(ev.get("portal") or {}).get("releaseId") or (ev.get("portal") or {}).get("kind")))
        elif kind == "tool_call":
            log.info("tool call", extra=kv(role=step.role, **_tool_line(ev)))
        elif kind == "tool_result":
            log.debug("tool result", extra=kv(role=step.role, tool=ev.get("name"), ok=ev.get("ok"), result=ev.get("result")))
        elif kind == "heartbeat":
            log.debug("heartbeat", extra=kv(role=step.role, elapsed=ev.get("elapsedSeconds")))
    proposal = final.get("proposal") if isinstance(final.get("proposal"), dict) else None
    outcome = RoleOutcome(
        role=step.role, status=str(final.get("status") or "error"), run_id=final.get("runId"),
        proposal_id=proposal.get("id") if proposal else None, saved=final.get("saved"),
        error=str(final["error"]) if final.get("error") else (str(final["saveError"]) if final.get("saveError") else None),
        elapsed=round(time.monotonic() - started, 1), stats={k: (final.get("stats") or {}).get(k) for k in ("searches", "fetches", "toolCalls")},
    )
    outcome.error_saving = bool(final.get("saveError")) and not step.request.get("dryRun")
    usage = final.get("usage") if isinstance(final.get("usage"), dict) else {}
    level = logging.WARNING if outcome.failed else logging.INFO
    log.log(level, "role finished", extra=kv(role=step.role, status=outcome.status, run_id=outcome.run_id, proposal_id=outcome.proposal_id,
                                             saved=outcome.saved, stop_reason=final.get("stopReason"), error=outcome.error,
                                             tokens=usage.get("totalTokens"), seconds=outcome.elapsed, **outcome.stats))
    return outcome


# ---------------------------------------------------------------------------------------------- the run
class DailyRun:
    """Runs steps one after another.  ``terminate()`` (SIGTERM) stops taking new roles and cancels the one running:
    a research run takes up to MAX_RUNTIME_SECONDS, far longer than the 30 s a terminated pod has."""

    def __init__(self, steps: list[Step], settings: Settings, *, model_factory: Callable[[Step], Any] | None = None):
        self.steps = steps
        self.settings = settings
        self.model_factory = model_factory
        self.terminated = False
        self._current: asyncio.Task | None = None

    def terminate(self) -> None:
        self.terminated = True
        if self._current is not None and not self._current.done():
            self._current.cancel()

    async def run(self) -> list[RoleOutcome]:
        outcomes: list[RoleOutcome] = []
        for step in self.steps:
            if self.terminated:
                outcomes.append(RoleOutcome(step.role, "skipped", error="the run was terminated before this role started"))
                continue
            model = self.model_factory(step) if self.model_factory else None
            self._current = asyncio.ensure_future(run_role(step, self.settings, model=model))
            try:
                outcomes.append(await self._current)
            except asyncio.CancelledError:
                if not self.terminated:  # somebody else cancelled the whole run
                    raise
                log.warning("role cancelled", extra=kv(role=step.role))
                outcomes.append(RoleOutcome(step.role, "cancelled", error="terminated (SIGTERM) while researching"))
            except Exception as e:  # noqa: BLE001 - one role must not stop the others (run_stream already turns errors into results)
                log.exception("role crashed", extra=kv(role=step.role))
                outcomes.append(RoleOutcome(step.role, "error", error=f"{type(e).__name__}: {e}"))
            finally:
                self._current = None
        return outcomes
