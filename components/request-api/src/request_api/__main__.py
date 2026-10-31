"""request-api entry point.

    python -m request_api serve          start the HTTP API on PORT (default) plus the key.* consumer thread
    python -m request_api check-config   validate settings and the request schema, then exit
"""
from __future__ import annotations

import os
import sys

from llmops_common.config import ConfigError
from llmops_common.log import get_logger, kv, setup_logging

log = get_logger("request_api")


def check_config():
    """Fail fast (before uvicorn starts) on a bad setting or schema."""
    from llmops_common.auth import Authenticator
    from request_api.settings import Settings
    from request_api.validation import RequestValidator

    settings = Settings.from_env()
    Authenticator.from_env()
    RequestValidator.load(settings.schema_path)
    return settings


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "request-api"))
    args = list(sys.argv[1:] if argv is None else argv) or ["serve"]
    if args[0] not in ("serve", "check-config"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        settings = check_config()
    except (ConfigError, ValueError, OSError) as exc:
        log.error("configuration error", extra=kv(error=str(exc)))
        return 2
    if args[0] == "serve":
        from llmops_common.fastapi_support import run

        run("request_api.main:app")
        return 0
    log.info("configuration ok", extra=kv(schema=str(settings.schema_path), worker=settings.run_worker,
                                          self_approval=settings.allow_self_approval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
