"""publisher: replays the research proposals on the live portal data and publishes releases (kind: worker).

    python -m publisher [worker]          consume queue "publisher" until SIGTERM; health on HEALTH_PORT (default 8081)
    python -m publisher once              process the jobs that are due, then exit (tests, catching up by hand)
    python -m publisher status            the live release, held releases, whether automatic publishing is paused

Operator commands (on AWS these were Step Functions executions with an "action"):
    python -m publisher approve RELEASE|PROPOSAL --by NAME [--proposal ID ...] [--sha256 H]   publish held proposals (re-validated)
    python -m publisher discard RELEASE|PROPOSAL --by NAME [--reason TEXT]                     take held proposals off the list
    python -m publisher rollback [--to RELEASE] --by NAME [--reason TEXT]   make an earlier release live (pauses auto publishing)
    python -m publisher pause --by NAME [--reason TEXT] | resume --by NAME
    python -m publisher bootstrap --from DIR|s3://bucket/prefix --by NAME [--allow-invalid]   the first release, from a portal checkout

Exit codes: 0 done · 1 refused (the reason is logged) · 2 usage · 3 configuration / environment problem
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

COMPONENT = "publisher"
QUEUE = "publisher"
EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_CONFIG = 0, 1, 2, 3

log = logging.getLogger("publisher")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m publisher", description=__doc__.split("\n\n")[0].strip(),
                                epilog="Settings come from the environment only; see components/publisher/README.md.",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("worker", help="consume the publisher queue (default)")
    sub.add_parser("once", help="process the jobs that are due, then exit")
    sub.add_parser("status", help="print the live release, held releases and the pause flag (JSON)")
    a = sub.add_parser("approve", help="publish held proposals: a held release id (all of them) or a proposal id")
    a.add_argument("release", metavar="RELEASE|PROPOSAL")
    a.add_argument("--proposal", action="append", default=[], help="of a held release, approve only this proposal id (repeatable)")
    a.add_argument("--sha256", help="the content that was reviewed (refused when the held proposal is something else)")
    d = sub.add_parser("discard", help="take held proposals off the list: a held release id or a proposal id")
    d.add_argument("release", metavar="RELEASE|PROPOSAL")
    r = sub.add_parser("rollback", help="make an earlier release live again")
    r.add_argument("--to", help="release id (default: the one before the live one)")
    r.add_argument("--if-current", help="only if this release is the live one")
    r.add_argument("--unlisted", action="store_true", help="allow a release older than the pointer's history")
    sub.add_parser("pause", help="hold every proposal for approval until resume")
    sub.add_parser("resume", help="publish automatically again")
    b = sub.add_parser("bootstrap", help="publish the first release from a portal checkout")
    b.add_argument("--from", dest="source", required=True, help="a portal checkout (schema/, data/, ...) or s3://bucket/prefix/")
    b.add_argument("--force", action="store_true", help="replace the live data even though a release exists")
    b.add_argument("--allow-invalid", action="store_true",
                   help="publish although the data validators object (they are listed as warnings; the files every page needs are still required)")
    b.add_argument("--note", default="", help="what this release is")
    for cmd in (a, d, r, sub.choices["pause"], sub.choices["resume"], b):
        cmd.add_argument("--by", required=True, help="who does this (recorded in the release history)")
    for cmd in (d, r, sub.choices["pause"]):
        cmd.add_argument("--reason", default="", help="why (recorded)")
    return p


def _publisher(store=None):
    from .handler import Publisher

    return Publisher(store)


def _print(obj) -> None:
    """One JSON line on stdout (stdout carries JSON lines only)."""
    print(json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def _worker(once: bool, store=None) -> int:
    from llmops_common.config import env_int
    from llmops_common.queue import Worker

    publisher = _publisher(store)
    worker = Worker(QUEUE, publisher.handle, health_port=None if once else env_int("HEALTH_PORT", 8081), ready_check=publisher.ready,
                    poll_seconds=2.0)
    if not once:
        worker.run()
        return EXIT_OK
    done = 0
    while done < 1000 and worker.run_once():
        done += 1
    log.info("jobs processed", extra={"fields": {"queue": QUEUE, "jobs": done}})
    return EXIT_OK


def _tree(source: str, store) -> dict[str, bytes]:
    """The files of a portal checkout: a directory, or an s3:// prefix in the object store (uploaded with any S3 tool)."""
    from . import releases as R

    if source.startswith("s3://"):
        bucket, _, prefix = source[len("s3://"):].partition("/")
        prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        files = {}
        for obj in store.list(bucket, prefix):
            rel = obj.key[len(prefix):]
            if R.publishable(rel):
                files[rel] = store.get_bytes(bucket, obj.key)
        return files
    root = Path(source)
    if not root.is_dir():
        raise ValueError(f"{source} is not a directory")
    files, skipped = R.collect_tree(root)
    for item in skipped[:50]:
        log.info("not published", extra={"fields": {"path": item.path, "why": item.why}})
    return files


def main(argv: list[str] | None = None, *, store=None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    from llmops_common.config import ConfigError
    from llmops_common.log import kv, setup_logging

    setup_logging(COMPONENT)
    command = args.command or "worker"
    try:
        if command in ("worker", "once"):
            return _worker(command == "once", store)
        return _operate(command, args, store)
    except ConfigError as e:
        log.error("configuration problem", extra=kv(error=str(e)))
        return EXIT_CONFIG


def _operate(command: str, args, store) -> int:
    from llmops_common.log import kv

    from . import publish as P
    from . import releases as R

    publisher = _publisher(store)
    now = R.utc_now()
    if command == "status":
        try:
            manifest, _ = R.read_pointer(publisher.portal)
        except R.NotBootstrapped:
            _print({"bootstrapped": False})
            return EXIT_OK
        _print({"bootstrapped": True, "current": manifest["current"], "published_at": manifest.get("published_at"),
                "sequence": manifest.get("sequence"), "paused": R.hold_of(manifest), "held": R.held_of(manifest),
                "history": [{k: h.get(k) for k in ("release_id", "published_at", "kind", "by", "summary", "changed")}
                            for h in manifest.get("history", [])[:10]]})
        return EXIT_OK
    with publisher.lock():
        try:
            if command == "approve":
                batch = P.approve(publisher.portal, publisher.control, args.release, actor=args.by, policy=publisher.settings.policy, now=now,
                                  only=args.proposal, expected_sha=args.sha256)
                releases, result = batch.releases, batch.summary()
                code = EXIT_OK if batch.published or all(i.status != "rejected" for i in batch.items) else EXIT_REFUSED
            elif command == "discard":
                outcome = P.discard(publisher.portal, args.release, actor=args.by, reason=args.reason, now=now, control=publisher.control)
                releases, result, code = [], outcome.to_state(), EXIT_OK if outcome.ok else EXIT_REFUSED
            elif command == "rollback":
                outcome = P.rollback(publisher.portal, args.to, actor=args.by, reason=args.reason, now=now, if_current=args.if_current,
                                     allow_unlisted=args.unlisted)
                releases, result, code = outcome.releases, outcome.to_state(), EXIT_OK if outcome.ok else EXIT_REFUSED
            elif command == "pause":
                outcome = P.pause(publisher.portal, actor=args.by, reason=args.reason, now=now)
                releases, result, code = [], outcome.to_state(), EXIT_OK
            elif command == "resume":
                outcome = P.resume(publisher.portal, actor=args.by, now=now)
                releases, result, code = [], outcome.to_state(), EXIT_OK
            elif command == "bootstrap":
                try:
                    tree = _tree(args.source, publisher.store)
                except (ValueError, R.ReleaseError) as e:
                    log.error("cannot read the portal checkout", extra=kv(error=str(e)))
                    return EXIT_USAGE
                outcome = P.publish_tree(publisher.portal, tree, bootstrap=True, force=args.force, allow_invalid=args.allow_invalid,
                                         actor=args.by, note=args.note, now=now)
                releases, result, code = outcome.releases, outcome.to_state(), EXIT_OK if outcome.status == "published" else EXIT_REFUSED
            else:  # pragma: no cover - argparse knows the commands
                return EXIT_USAGE
        except R.ReleaseError as e:
            log.error(f"{command} refused", extra=kv(error=str(e)))
            return EXIT_REFUSED
    if releases:
        publisher.announce(releases)
    level = logging.INFO if code == EXIT_OK else logging.WARNING
    log.log(level, f"{command} finished", extra=kv(result=result))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
