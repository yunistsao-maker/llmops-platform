"""test-runner worker.

    python -m test_runner            consume queue `test-runner` (default)
    python -m test_runner worker     same

Health: GET /healthz and /readyz on HEALTH_PORT (default 8081). SIGTERM: no new jobs; a running job stops launching
cases and is re-queued at once (finished items are kept), so the pod exits within the grace period.
"""
from __future__ import annotations

import os
import sys
import threading

from llmops_common import db
from llmops_common.cloud import configure_aws_roles_anywhere
from llmops_common.config import env_float, env_int
from llmops_common.log import get_logger, kv, setup_logging
from llmops_common.queue import Worker
from llmops_common.storage import S3Store, bucket

from .judge import JudgeFactory
from .providers import ProviderFactory
from .runner import JobRunner, RunnerConfig

log = get_logger("test_runner")
QUEUE = "test-runner"


class RunnerWorker(Worker):
    """Worker whose SIGTERM also cancels the job in progress."""

    def __init__(self, *args, cancel: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel = cancel

    def stop(self, *args):
        super().stop(*args)
        self.cancel.set()


def build_worker(store=None, providers=None, judges=None) -> RunnerWorker:
    store = store or S3Store.from_env()
    cancel = threading.Event()

    def ready():
        db.ping()
        store.ping(bucket("datasets"))

    worker = RunnerWorker(QUEUE, lambda job: None, cancel=cancel, poll_seconds=env_float("POLL_SECONDS", 2.0),
                          lease_seconds=env_int("JOB_LEASE_SECONDS", 900), health_port=env_int("HEALTH_PORT", 8081),
                          ready_check=ready)
    worker.handler = JobRunner(store=store, providers=providers or ProviderFactory(), judges=judges or JudgeFactory(),
                               config=RunnerConfig.from_env(), worker_id=worker.worker_id, cancel=cancel)
    return worker


def main(argv: list[str] | None = None) -> int:
    configure_aws_roles_anywhere()  # direct Bedrock routes: short-lived credentials from the certificate
    setup_logging(os.environ.get("COMPONENT", "test-runner"))
    args = list(sys.argv[1:] if argv is None else argv) or ["worker"]
    if args[0] != "worker":
        print(__doc__, file=sys.stderr)
        return 2
    worker = build_worker()
    log.info("test-runner starting", extra=kv(queue=QUEUE, concurrency=worker.handler.config.concurrency,
                                              lease_seconds=worker.lease_seconds))
    worker.run()
    # Case threads still waiting on a model call must not hold the pod past its grace period: the job was re-queued.
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
