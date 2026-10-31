"""results-publisher worker.

    python -m results_publisher                 consume queue `results-publisher` (default)
    python -m results_publisher worker          same
    python -m results_publisher publish <run>   (re)build the portal security file of one run and exit

Health: GET /healthz and /readyz on HEALTH_PORT (default 8081).
"""
from __future__ import annotations

import os
import sys

from llmops_common import db
from llmops_common.config import env_float, env_int
from llmops_common.log import get_logger, setup_logging
from llmops_common.queue import Worker
from llmops_common.storage import S3Store, bucket

from .publisher import Handler, publish_run

log = get_logger("results_publisher")
QUEUE = "results-publisher"


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "results-publisher"))
    args = list(sys.argv[1:] if argv is None else argv) or ["worker"]
    store = S3Store.from_env()
    if args[0] == "worker":
        def ready():
            db.ping()
            store.ping(bucket("curated"))

        Worker(QUEUE, Handler(store, db.connection), poll_seconds=env_float("POLL_SECONDS", 2.0),
               health_port=env_int("HEALTH_PORT", 8081), ready_check=ready).run()
        return 0
    if args[0] == "publish" and len(args) == 2:
        with db.connection() as conn:
            out = publish_run(conn, store, args[1])
        print(f"{out['key']} written; pointer moved: {out['pointer_moved']}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
