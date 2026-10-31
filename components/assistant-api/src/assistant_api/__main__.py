"""python -m assistant_api  ->  uvicorn on PORT (default 8080)."""
from __future__ import annotations

import os

from llmops_common.log import setup_logging


def main() -> None:
    setup_logging(os.environ.get("COMPONENT", "assistant-api"))
    from llmops_common.fastapi_support import run

    run("assistant_api.main:app")


if __name__ == "__main__":
    main()
