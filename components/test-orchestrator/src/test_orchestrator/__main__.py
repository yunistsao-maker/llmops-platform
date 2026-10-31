"""test-orchestrator.

    python -m test_orchestrator              serve: API on PORT (default 8080) + queue worker + gateway watcher
    python -m test_orchestrator serve        same
    python -m test_orchestrator sync-routes  load ROUTES_FILE into tests.routes and exit
"""
from __future__ import annotations

import os
import sys

from llmops_common.log import get_logger, setup_logging

log = get_logger("test_orchestrator")


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "test-orchestrator"))
    args = list(sys.argv[1:] if argv is None else argv) or ["serve"]
    if args[0] == "serve":
        from llmops_common.fastapi_support import run

        run("test_orchestrator.app:app")
        return 0
    if args[0] == "sync-routes":
        from llmops_common import db
        from llmops_common.storage import S3Store

        from .service import Orchestrator

        orch = Orchestrator(S3Store.from_env())
        with db.connection() as conn:
            orch.sync_routes(conn)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
