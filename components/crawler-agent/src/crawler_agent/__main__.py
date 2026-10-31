import logging
import os
import sys

from crawler_agent.cli import EXIT_TERMINATED, main

if __name__ == "__main__":
    code = main()
    if code == EXIT_TERMINATED:
        # Terminated (SIGTERM): the result of a fetch still running in a worker thread is not wanted; do not wait for it.
        logging.shutdown()
        sys.stdout.flush()
        os._exit(code)
    sys.exit(code)
