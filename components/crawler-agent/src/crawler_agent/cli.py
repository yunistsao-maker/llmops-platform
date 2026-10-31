"""crawler-agent: the LLMOps research agent, run as a (cron)job.

    python -m crawler_agent daily [--roles ROLE[,ROLE...]] [--dry-run] [--no-event]
        the eight research roles one after another (the daily plan), then research.proposal.ready
    python -m crawler_agent role ROLE [--request JSON] [--dry-run] [--no-event]
        one role with its daily request (--request changes it, e.g. '{"instruction": "...", "urls": [...]}')
    python -m crawler_agent roles
        print the daily plan (needs no settings)
    python -m crawler_agent announce --payload JSON
        publish research.proposal.ready again with the payload a run logged when it could not (database outage)

--dry-run   research only: no result, handoff note or event is written (the outcome is logged)
--no-event  save the results but do not publish research.proposal.ready

Exit codes: 0 every role ran · 1 a role failed or the event could not be published (the other roles ran and what
they produced is saved) · 2 usage · 3 configuration / environment problem (nothing ran) · 143 terminated (SIGTERM)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any, Callable

from .plan import ROLE_ORDER, DAILY

COMPONENT = "crawler-agent"
EXIT_OK, EXIT_INCOMPLETE, EXIT_USAGE, EXIT_CONFIG, EXIT_TERMINATED = 0, 1, 2, 3, 143

log = logging.getLogger("crawler_agent")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m crawler_agent", description=__doc__.split("\n\n")[0].strip(),
                                epilog="Settings come from the environment only; see components/crawler-agent/README.md.",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    d = sub.add_parser("daily", help="run the daily plan (all roles, or --roles), then publish research.proposal.ready")
    d.add_argument("--roles", help=f"comma-separated subset, kept in the daily order ({', '.join(ROLE_ORDER)})")
    r = sub.add_parser("role", help="run one role with its daily request")
    r.add_argument("name", choices=ROLE_ORDER, metavar="ROLE", help=f"one of: {', '.join(ROLE_ORDER)}")
    r.add_argument("--request", help="JSON object merged into the role's daily request (agent request keys)")
    for cmd in (d, r):
        cmd.add_argument("--dry-run", action="store_true", help="research only: write nothing, publish nothing")
        cmd.add_argument("--no-event", action="store_true", help="save results but do not publish research.proposal.ready")
    sub.add_parser("roles", help="print the daily plan")
    a = sub.add_parser("announce", help="publish research.proposal.ready with a payload a run logged")
    a.add_argument("--payload", required=True, help='{"run_date": ..., "run_id": ..., "proposal_keys": [...], "roles": [...]}')
    return p


def _announce(raw: str) -> int:
    from llmops_common.log import kv

    from . import events

    try:
        body = json.loads(raw)
        events.check_payload(body)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    try:
        events.publish_ready(body)
    except Exception as e:  # noqa: BLE001
        log.error("could not publish research.proposal.ready", extra=kv(error=f"{type(e).__name__}: {e}"))
        return EXIT_CONFIG
    return EXIT_OK


class ConfigProblem(Exception):
    pass


def _settings(events: bool, dry_run: bool):
    """The agent's settings, checked before anything runs (a wrong setting must not cost an hour of research)."""
    from llmops_common.config import ConfigError, env

    from research.config import Settings, curated_bucket
    from research.web.search_backend import PROVIDERS, known_backends

    try:
        settings = Settings.from_env()
    except OSError as e:  # an unreadable *_FILE
        raise ConfigProblem(str(e)) from e
    if settings.model_provider not in ("gateway", "scripted"):
        raise ConfigProblem(f"MODEL_PROVIDER must be gateway or scripted, got {settings.model_provider!r}")
    if settings.model_provider == "gateway":
        from model.load import openai_base_url

        try:
            openai_base_url(settings.gateway_url)
        except ValueError as e:
            raise ConfigProblem(str(e)) from e
    if settings.search_backend != "none" and settings.search_backend not in PROVIDERS:
        raise ConfigProblem(f"SEARCH_BACKEND={settings.search_backend!r} is not available; known: {', '.join(known_backends())}")
    if not dry_run and not settings.output_uri:
        raise ConfigProblem("OUTPUT_URI is empty: results would not be kept")
    if events:
        bucket = curated_bucket(os.environ)
        if not settings.output_uri.startswith(f"s3://{bucket}/"):
            raise ConfigProblem(f"OUTPUT_URI ({settings.output_uri}) must be in the curated bucket s3://{bucket}/: the publisher reads "
                                "proposals from there (or use --no-event)")
        try:
            env("DATABASE_URL")
        except ConfigError as e:
            raise ConfigProblem(f"{e}: needed to publish research.proposal.ready (or use --no-event)") from e
    return settings


def _preflight(settings) -> str:
    """Can the portal data be read?  Returns a short description of the release in use."""
    from research.portal.data import PortalData

    try:
        portal = PortalData.load(settings.portal_data)
    except FileNotFoundError as e:
        raise ConfigProblem(f"no portal data at {settings.portal_data} ({e}). On a new platform publish the first release "
                            "with the publisher (python -m publisher bootstrap --from <portal checkout>)") from e
    except Exception as e:  # noqa: BLE001 - storage unreachable, credentials, a malformed pointer ...
        raise ConfigProblem(f"cannot read the portal data at {settings.portal_data}: {type(e).__name__}: {e}") from e
    m = portal.manifest
    if portal.is_sample or m.get("kind") not in ("release", "s3"):
        log.warning("portal data is not a published release: proposals made against it cannot be published",
                    extra={"fields": {"portal": m.get("kind"), "source": m.get("source")}})
    return str(m.get("releaseId") or m.get("kind") or "?")


def _run(steps, settings, model_factory) -> tuple[list, bool]:
    from .daily import DailyRun

    runner = DailyRun(steps, settings, model_factory=model_factory)
    loop = asyncio.new_event_loop()
    handled = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runner.terminate)
            handled.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):  # not the main thread (tests)
            pass
    try:
        outcomes = loop.run_until_complete(runner.run())
    finally:
        for sig in handled:
            loop.remove_signal_handler(sig)
        if not runner.terminated:  # a terminated run does not wait for a fetch still running in a worker thread
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
    return outcomes, runner.terminated


def main(argv: list[str] | None = None, *, model_factory: Callable[[Any], Any] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:  # --help (0) or a usage error (2)
        return int(e.code or 0)
    if args.command == "roles":
        for step in DAILY:
            print(f"{step.role}\t{json.dumps(step.request, ensure_ascii=False)}")
        return EXIT_OK

    from llmops_common.ids import new_run_id
    from llmops_common.log import kv, setup_logging

    setup_logging(COMPONENT)
    if args.command == "announce":
        return _announce(args.payload)
    from research.clock import today
    from research.config import curated_bucket

    from . import events
    from .plan import plan, step_for

    dry_run = bool(args.dry_run)
    publish_event = not dry_run and not args.no_event
    try:
        if args.command == "role":
            overrides = json.loads(args.request) if args.request else None
            steps = [step_for(args.name, overrides)]
        else:
            steps = plan(args.roles.split(",") if args.roles else None)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    if dry_run:
        steps = [type(s)(s.role, {**s.request, "dryRun": True}) for s in steps]
    try:
        settings = _settings(publish_event, dry_run)
        release = _preflight(settings)
    except ConfigProblem as e:
        log.error("not started: configuration problem", extra=kv(error=str(e)))
        return EXIT_CONFIG

    run_id, run_date = new_run_id(), today().isoformat()
    log.info("research run started", extra=kv(run_id=run_id, run_date=run_date, roles=[s.role for s in steps], release=release,
                                              dry_run=dry_run, events=publish_event, model=settings.model_id,
                                              provider=settings.model_provider, search=settings.search_backend))
    outcomes, terminated = _run(steps, settings, model_factory)

    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    code = EXIT_TERMINATED if terminated else (EXIT_INCOMPLETE if any(o.failed for o in outcomes) else EXIT_OK)
    body = events.payload(run_date=run_date, run_id=run_id, outcomes=outcomes, curated_bucket=curated_bucket(os.environ),
                          roles=[s.role for s in steps])
    if publish_event:
        try:
            events.publish_ready(body)
        except Exception as e:  # noqa: BLE001 - the research is saved; say exactly what to send again
            log.error("could not publish research.proposal.ready; the proposals are saved - publish this payload again",
                      extra=kv(error=f"{type(e).__name__}: {e}", payload=body))
            code = EXIT_TERMINATED if terminated else EXIT_INCOMPLETE
    log.info("research run finished", extra=kv(run_id=run_id, exit_code=code, counts=counts, proposal_keys=body["proposal_keys"],
                                               failed=[o.role for o in outcomes if o.failed]))
    return code
