"""Run one research task: payload in, stream of events + one validated proposal out."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import time
import uuid
from typing import Any, AsyncIterator

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.executors import SequentialToolExecutor

from . import handoff
from .clock import iso_now, today
from .config import Settings
from .events import EventMapper
from .portal.data import PortalData
from .prompts import KNOWN_FOCUS, KNOWN_KINDS, SYSTEM_PROMPT, build_task_prompt
from .session import ResearchSession
from .skills import ROLES, playbook
from .store import save_result
from .tools import build_tools
from .web.safe_http import SafeFetcher
from .web.search_backend import SearchBackend, build_search_backend

log = logging.getLogger(__name__)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_WEEKLY_WORDS = {"", "weekly", "weekly research", "run weekly", "run the weekly research"}
_CLI_KEYS = {"user_id", "payment_instrument_id", "payment_session_id"}  # added by `agentcore invoke` (AWS era), not ours


# ---------------------------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------------------------
def parse_payload(payload: Any) -> dict:
    """Validate the invocation payload.  Accepts ``{"prompt": "..."}`` (as sent by ``agentcore invoke``) or a structured task."""
    if isinstance(payload, str):
        payload = {"prompt": payload}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object (or a string prompt)")
    payload = {k: v for k, v in payload.items() if k not in _CLI_KEYS}
    # `agentcore invoke "<text>"` always sends {"prompt": "<text>"}: a JSON object passed that way is the real payload
    p = payload.get("prompt")
    if set(payload) == {"prompt"} and isinstance(p, str) and p.lstrip().startswith("{"):
        try:
            inner = json.loads(p)
        except json.JSONDecodeError as e:
            raise ValueError(f"prompt starts with '{{' but is not valid JSON ({e.msg} at char {e.pos})") from None
        if not isinstance(inner, dict):
            raise ValueError("a JSON prompt must be an object")
        payload = inner
    for banned in ("messages", "tool_results"):
        if banned in payload:
            raise ValueError(f"`{banned}` is not supported: every invocation is a fresh, single-shot research run")
    known = {"prompt", "kind", "role", "instruction", "focus", "claims", "urls", "periodStart", "periodEnd", "includeDigest", "dryRun", "budget",
             "background"}
    unknown = set(payload) - known
    if unknown:
        raise ValueError(f"unknown payload keys {sorted(unknown)}; allowed: {sorted(known)}")
    prompt = payload.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    instruction = payload.get("instruction")
    if instruction is not None and not isinstance(instruction, str):
        raise ValueError("instruction must be a string")
    kind = payload.get("kind")
    if kind is None:
        kind = "weekly" if (prompt or "").strip().lower() in _WEEKLY_WORDS and not instruction else "prompt"
    if kind not in KNOWN_KINDS:
        raise ValueError(f"kind must be one of {list(KNOWN_KINDS)}")
    task: dict[str, Any] = {"kind": kind}
    role_name = payload.get("role")
    if role_name is not None:
        if not isinstance(role_name, str) or role_name not in ROLES:
            raise ValueError(f"unknown role {str(role_name)[:60]!r}; known: {sorted(ROLES)}")
        task["role"] = role_name
    text = (instruction or prompt or "").strip()
    if kind == "prompt" and not text:
        raise ValueError("kind 'prompt' needs a prompt/instruction")
    if text and not (kind == "weekly" and text.lower() in _WEEKLY_WORDS):
        task["instruction"] = text[:4000]
    focus = payload.get("focus")
    if focus is not None:
        if not isinstance(focus, list) or not all(isinstance(f, str) for f in focus):
            raise ValueError("focus must be a list of strings")
        bad = [f for f in focus if f not in KNOWN_FOCUS]
        if bad:
            raise ValueError(f"unknown focus {bad}; known: {KNOWN_FOCUS}")
        task["focus"] = focus
    if kind in ("focus", "weekly") and not task.get("focus") and task.get("role"):
        task["focus"] = list(ROLES[task["role"]].focus)  # a role knows what it researches
    if kind == "focus" and not task.get("focus"):
        raise ValueError("kind 'focus' needs a non-empty focus list")
    claims = payload.get("claims")
    if claims is not None:
        if not isinstance(claims, list) or not claims or len(claims) > 40:
            raise ValueError("claims must be a list of 1-40 items")
        task["claims"] = claims
    if kind == "verify" and not task.get("claims"):
        raise ValueError("kind 'verify' needs claims")
    urls = payload.get("urls")
    if urls is not None:
        if not isinstance(urls, list) or len(urls) > 30 or not all(isinstance(u, str) and u.startswith("https://") for u in urls):
            raise ValueError("urls must be a list of up to 30 https:// URLs")
        task["urls"] = urls
    for key in ("periodStart", "periodEnd"):
        v = payload.get(key)
        if v is not None:
            if not isinstance(v, str) or not _DATE.match(v):
                raise ValueError(f"{key} must be YYYY-MM-DD")
            dt.date.fromisoformat(v)
            task[key] = v
    if "includeDigest" in payload:
        task["includeDigest"] = bool(payload["includeDigest"])
    task["dryRun"] = bool(payload.get("dryRun", False))
    if payload.get("background"):
        if task["dryRun"]:
            raise ValueError("background runs return nothing to the caller: they cannot be combined with dryRun")
        task["background"] = True
    budget = payload.get("budget")
    if budget is not None:
        if not isinstance(budget, dict) or set(budget) - {"searches", "fetches", "toolCalls"} or not all(isinstance(v, int) and v > 0 for v in budget.values()):
            raise ValueError("budget may set searches / fetches / toolCalls to positive integers (it can only lower the deployment's limits)")
        task["budget"] = budget
    return task


# ---------------------------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------------------------
def build_fetcher(settings: Settings) -> SafeFetcher:
    behind_proxy = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    return SafeFetcher(timeout=settings.fetch_timeout, max_bytes=settings.fetch_max_bytes, via_proxy=behind_proxy)


def build_renderer(settings: Settings):
    """JavaScript rendering used the AgentCore Browser on AWS; the platform has none, so ``fetch_page(render=true)``
    answers that rendering is not enabled and the model reads the page's raw / JSON / RSS variant or another source."""
    return None


def effective_limits(settings: Settings, task: dict) -> Settings:
    """A request may lower the deployment's budgets, never raise them."""
    from dataclasses import replace

    b = task.get("budget") or {}
    return replace(
        settings,
        max_searches=min(settings.max_searches, b.get("searches", settings.max_searches)),
        max_fetches=min(settings.max_fetches, b.get("fetches", settings.max_fetches)),
        max_tool_calls=min(settings.max_tool_calls, b.get("toolCalls", settings.max_tool_calls)),
    )


def new_session(settings: Settings, task: dict, *, portal: PortalData | None = None, fetcher: SafeFetcher | None = None,
                search: SearchBackend | None = None, renderer: Any = None) -> ResearchSession:
    portal = portal or PortalData.load(settings.portal_data)
    session = ResearchSession(
        settings, portal,
        fetcher=fetcher or build_fetcher(settings),
        search=search or build_search_backend(settings, portal.sources),
        renderer=renderer if renderer is not None else build_renderer(settings),
        task=task,
    )
    session.remember_urls(task.get("urls", []))
    return session


def system_prompt(task: dict) -> str:
    """The hard rules and the proposal format, then - for a role - the role's playbook (its skills)."""
    return SYSTEM_PROMPT + ("\n\n" + playbook(task["role"]) if task.get("role") else "")


def build_agent(session: ResearchSession, model: Any) -> Agent:
    return Agent(
        model=model,
        system_prompt=system_prompt(session.task or {}),
        tools=build_tools(session),
        callback_handler=None,  # events are streamed by run_stream, nothing is printed
        conversation_manager=SlidingWindowConversationManager(window_size=100, should_truncate_results=True),
        # the session's counters/pages are plain Python state: run tool calls one at a time (the model can still batch calls in a turn)
        tool_executor=SequentialToolExecutor(),
        load_tools_from_directory=False,
    )


def new_run_id() -> str:
    return "run-" + iso_now().replace("-", "").replace(":", "") + "-" + uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------
async def _pump(stream: Any, queue: asyncio.Queue) -> None:
    """Feed a Strands event stream into ``queue`` as ("ev", event) ... then ("end", None) or ("exc", error)."""
    try:
        async for ev in stream:
            await queue.put(("ev", ev))
        await queue.put(("end", None))
    except asyncio.CancelledError:
        raise
    except BaseException as e:  # noqa: BLE001 - forwarded to the consumer, which re-raises it
        await queue.put(("exc", e))
    finally:
        await stream.aclose()


async def run_stream(
    payload: Any,
    *,
    settings: Settings | None = None,
    model: Any = None,
    portal: PortalData | None = None,
    fetcher: SafeFetcher | None = None,
    search: SearchBackend | None = None,
    renderer: Any = None,
    run_id: str | None = None,
    heartbeat: float = 15.0,
) -> AsyncIterator[dict]:
    """Yield progress events, then exactly one ``{"type": "result", ...}`` event."""
    settings = settings or Settings.from_env()
    run_id = run_id or new_run_id()
    started = time.monotonic()
    try:
        task = parse_payload(payload)
    except ValueError as e:
        yield {"type": "result", "status": "error", "runId": run_id, "error": f"invalid request: {e}"}
        return

    settings = effective_limits(settings, task)
    session = None
    status, error, stop_reason, usage = "error", None, None, {}
    try:
        if model is None:
            from model.load import load_model

            model = load_model(settings)
        session = new_session(settings, task, portal=portal, fetcher=fetcher, search=search, renderer=renderer)
        agent = build_agent(session, model)
        limits = {"searches": settings.max_searches, "fetches": settings.max_fetches, "toolCalls": settings.max_tool_calls}
        # what the earlier roles of the day (and yesterday's) left for this one: data, read from the result store (never raises)
        notes = handoff.notes_for(settings.output_uri, task["role"], today(), session) if task.get("role") else ""
        prompt = build_task_prompt(task, session.portal.summary(), session.portal.docs, today(), limits, handoff=notes)
        yield {"type": "run_started", "runId": run_id, "task": {k: v for k, v in task.items() if k != "claims"}, "model": settings.model_id,
               "provider": settings.model_provider, "portal": session.portal.summary()["manifest"], "budgets": limits,
               "search": getattr(session.search, "name", "?")}

        mapper = EventMapper()
        stream = agent.stream_async(prompt, limits={"turns": settings.max_turns, "total_tokens": settings.max_total_tokens})
        # The Strands stream is consumed by ONE task from start to finish (OpenTelemetry context tokens must be attached and
        # detached in the same context); this generator only reads its queue, so it can time out / heartbeat without
        # interrupting the model call or a fetch.
        queue: asyncio.Queue = asyncio.Queue()
        pump = asyncio.ensure_future(_pump(stream, queue))
        try:
            while True:
                try:
                    kind, item = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except asyncio.TimeoutError:  # a slow model call or fetch: keep the connection warm, enforce the wall-clock limit
                    elapsed = time.monotonic() - started
                    if elapsed > settings.max_runtime_seconds:
                        stop_reason, error = "timeout", f"stopped after {settings.max_runtime_seconds}s (MAX_RUNTIME_SECONDS)"
                        break
                    yield {"type": "heartbeat", "elapsedSeconds": round(elapsed)}
                    continue
                if kind == "end":
                    break
                if kind == "exc":
                    raise item
                for out in mapper.map(item):
                    yield out
                if isinstance(item, dict) and "result" in item:
                    res = item["result"]
                    stop_reason = getattr(res, "stop_reason", None)
                    try:
                        usage = dict(res.metrics.accumulated_usage)
                    except Exception:
                        usage = {}
                if time.monotonic() - started > settings.max_runtime_seconds:
                    stop_reason, error = "timeout", f"stopped after {settings.max_runtime_seconds}s (MAX_RUNTIME_SECONDS)"
                    break
        finally:
            if not pump.done():
                pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        status = "accepted" if session.proposal else "no_proposal"
        if status == "accepted":
            stop_reason = "proposal_accepted"
        elif not error:
            error = {
                "limit_turns": f"stopped after {settings.max_turns} model turns (MAX_TURNS)",
                "limit_total_tokens": f"stopped after {settings.max_total_tokens} tokens (MAX_TOTAL_TOKENS)",
            }.get(stop_reason or "", "the model finished without an accepted proposal")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # model/AWS/network errors become a result, not a crashed stream
        log.exception("research run failed")
        status, error = "error", f"{type(e).__name__}: {e}"

    result: dict[str, Any] = {
        "type": "result", "status": status, "runId": run_id, "finishedAt": iso_now(),
        "elapsedSeconds": round(time.monotonic() - started, 1), "stopReason": stop_reason, "usage": usage,
        "task": {k: v for k, v in task.items() if k != "claims"},
    }
    if error:
        result["error"] = error
    if session is not None:
        result["stats"] = session.stats()
        if session.proposal:
            result["proposal"] = session.proposal
        if session.last_report:
            result["report"] = session.last_report
        if not session.proposal:  # keep the research even when no proposal was accepted
            result["evidence"] = [
                {"id": e.id, "url": e.url, "tier": e.tier.verification, "quote": e.quote, "note": e.note}
                for e in session.evidence.values()
            ]
        if not task.get("dryRun"):
            if task.get("role") and settings.output_uri:
                # what the next roles (and tomorrow's run) should know - whatever this run produced; a failure is logged only
                note = handoff.save(settings.output_uri, result, session, today=today())
                if note:
                    result["handoff"] = note
            try:
                loc = save_result(settings.output_uri, result)
                if loc:
                    result["saved"] = loc
            except Exception as e:
                log.exception("saving result failed")
                result["saveError"] = f"{type(e).__name__}: {e}"
    yield result


async def run_once(payload: Any, **kw: Any) -> dict:
    """Collect a run: returns the final result event (and keeps the progress events under ``events``)."""
    events: list[dict] = []
    final: dict = {}
    async for ev in run_stream(payload, **kw):
        if ev.get("type") == "result":
            final = ev
        else:
            events.append(ev)
    final["events"] = events
    return final
