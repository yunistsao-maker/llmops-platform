import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/llmops-common/src"))
sys.path.insert(0, str(ROOT / "components/assistant-api/src"))

# the prototype's data-v3.json: the contract fixture plus availability, gateway info and ZDR (view-builder 2026-10-30)
VIEW = Path(__file__).parent / "golden/view.json"
GOLDEN = Path(__file__).parent / "golden/search.json"


@pytest.fixture(scope="session")
def view():
    return json.loads(VIEW.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def golden():
    """Top-8 hits and the context block from the prototype page's own JavaScript (app3.html buildIndex/search)
    run under Node against golden/view.json."""
    return json.loads(GOLDEN.read_text(encoding="utf-8"))
