"""key-provisioner entry point.

    python -m key_provisioner run            consume queue `key-provisioner` (default); health on HEALTH_PORT
    python -m key_provisioner check-config   check settings, gateway admin access and the database, then exit
"""
from __future__ import annotations

import os
import sys

from llmops_common.config import ConfigError, env
from llmops_common.log import get_logger, kv, setup_logging

log = get_logger("key_provisioner")


def build():
    from key_provisioner.litellm import LiteLLMAdmin
    from key_provisioner.provisioner import Provisioner
    from key_provisioner.settings import Settings
    from key_provisioner.sinks import sink_from_env

    settings = Settings.from_env()
    admin = LiteLLMAdmin(settings.litellm_url, lambda: env("LITELLM_MASTER_KEY"), timeout=settings.litellm_timeout)
    return settings, Provisioner(settings, admin, sink_from_env(settings.secret_sink))


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "key-provisioner"))
    args = list(sys.argv[1:] if argv is None else argv) or ["run"]
    if args[0] not in ("run", "check-config"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        settings, provisioner = build()
    except ConfigError as exc:
        log.error("configuration error", extra=kv(error=str(exc)))
        return 2
    if args[0] == "run":
        from llmops_common.queue import Worker

        from key_provisioner.provisioner import QUEUE

        if settings.secret_sink == "file":
            log.warning("SECRET_SINK=file is for local development only")
        Worker(QUEUE, provisioner.handle, poll_seconds=settings.poll_seconds, health_port=settings.health_port,
               ready_check=provisioner.ready).run()
        return 0
    from llmops_common import db

    try:
        provisioner.admin.check_access()
        db.ping()
    except Exception as exc:  # noqa: BLE001 - report any dependency problem as a failed check
        log.error("check failed", extra=kv(error=f"{type(exc).__name__}: {exc}"))
        return 1
    log.info("configuration ok", extra=kv(sink=settings.secret_sink, litellm=settings.litellm_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
