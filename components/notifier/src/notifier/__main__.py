"""notifier entry point.

    python -m notifier run            consume queue `notifier` (default); health on HEALTH_PORT
    python -m notifier check-config   validate settings and the database connection, then exit
"""
from __future__ import annotations

import os
import sys

from llmops_common.config import ConfigError
from llmops_common.log import get_logger, kv, setup_logging

log = get_logger("notifier")


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "notifier"))
    args = list(sys.argv[1:] if argv is None else argv) or ["run"]
    from notifier.handler import QUEUE, Notifier
    from notifier.settings import Settings

    if args[0] not in ("run", "check-config"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        log.error("configuration error", extra=kv(error=str(exc)))
        return 2
    notifier = Notifier(settings)
    summary = kv(dry_run=settings.dry_run, smtp=bool(settings.smtp_host), webhooks=len(settings.webhook_urls),
                 admins=len(settings.admins), reviewers_g2=len(settings.reviewers["G2"]),
                 reviewers_g8=len(settings.reviewers["G8"]))
    if args[0] == "run":
        from llmops_common.queue import Worker

        if settings.dry_run:
            log.warning("NOTIFY_DRY_RUN is on: notifications are only logged", extra=summary)
        else:
            log.info("notifier configured", extra=summary)
        Worker(QUEUE, notifier.handle, poll_seconds=settings.poll_seconds, health_port=settings.health_port,
               ready_check=notifier.ready).run()
        return 0
    try:
        notifier.ready()
    except Exception as exc:  # noqa: BLE001 - report any dependency problem as a failed check
        log.error("check failed", extra=kv(error=f"{type(exc).__name__}: {exc}"))
        return 1
    log.info("configuration ok", extra=summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
