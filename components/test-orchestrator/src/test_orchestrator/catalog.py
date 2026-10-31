"""contracts/sec-catalog.json (copied into the image at /app/contracts/sec-catalog.json; SEC_CATALOG_PATH overrides)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from llmops_common.config import env

IMAGE_PATH = Path("/app/contracts/sec-catalog.json")


def catalog_path() -> Path:
    """SEC_CATALOG_PATH, else the copy in the image, else the repo's contracts/ (development and tests)."""
    configured = env("SEC_CATALOG_PATH", None)
    if configured:
        return Path(configured)
    if IMAGE_PATH.exists():
        return IMAGE_PATH
    parents = Path(__file__).resolve().parents  # <repo>/components/test-orchestrator/src/test_orchestrator/catalog.py
    return parents[min(4, len(parents) - 1)] / "contracts" / "sec-catalog.json"


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_catalog(path: str | Path | None = None) -> dict:
    return _load(str(path or catalog_path()))


def items_by_id(catalog: dict) -> dict[str, dict]:
    return {item["sec_id"]: item for item in catalog["items"]}


def is_gateway_check(item: dict) -> bool:
    """A platform item with no cases (SEC-16): one live check per gateway, not one per route."""
    return item.get("layer") == "platform" and int(item.get("n") or 0) == 0


def is_derived(item: dict) -> bool:
    """Computed from the other results of the run (SEC-11: response.model and stop_reason of every call)."""
    return item.get("layer") == "logged"
