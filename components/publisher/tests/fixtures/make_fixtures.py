#!/usr/bin/env python3
"""Regenerate fixtures/accepted_run.json: a real run record, produced by the real agent stack with a scripted model.

Run it with the crawler-agent's dependencies installed (from components/publisher):

    python tests/fixtures/make_fixtures.py

The run is researched against the release that ``conftest.snapshot_tree()`` bootstraps at ``conftest.T0``.  The
proposal id depends on that release's id, so after regenerating, replace the old id in tests/*.py (conftest.PROPOSAL_ID
checks it).  The committed fixture was made on AWS (its base release id has the old shape, which is still accepted).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
os.environ["RESEARCH_NOW"] = "2026-10-02T03:00:00Z"  # the same pinned clock as the agent's own tests (the script cites this day)
os.environ["RESEARCH_TODAY"] = "2026-10-02"
sys.path[:0] = [str(HERE.parent), str(REPO / "components/publisher/src"), str(REPO / "components/crawler-agent/src"),
                str(REPO / "components/crawler-agent/tests"), str(REPO / "libs/llmops-common/src")]

import conftest as C  # noqa: E402
from e2e_helpers import FOUNDRY_HTML, FOUNDRY_URL, Site, foundry_script, settings  # noqa: E402
from llmops_common.storage import MemoryStore  # noqa: E402
from research import objectstore  # noqa: E402
from research.agent import run_once  # noqa: E402
from research.offline.scripted_model import ScriptedModel  # noqa: E402
from research.web.search_backend import FakeSearchBackend  # noqa: E402


def main() -> None:
    plane = C.Plane(MemoryStore(), "memory")
    objectstore.set_store(plane.store)
    release = plane.bootstrap().release_id
    st = settings(portal_data=f"s3://{C.BUCKET}/portal/", output_uri=f"s3://{C.BUCKET}/research/")
    site = Site().add(FOUNDRY_URL, FOUNDRY_HTML)
    res = asyncio.run(run_once({"prompt": "Check the Foundry retirement date of claude-sonnet-4-5", "kind": "prompt"}, settings=st,
                               model=ScriptedModel(foundry_script()), fetcher=site.fetcher(), search=FakeSearchBackend({})))
    assert res["status"] == "accepted", res.get("error") or res.get("report")
    saved = json.loads(plane.store.get_bytes(C.BUCKET, f"research/runs/{res['runId']}.json"))
    assert saved["proposal"]["portal"]["manifest"]["releaseId"] == release, saved["proposal"]["portal"]
    (HERE / "accepted_run.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote accepted_run.json: run {saved['runId']}, proposal {saved['proposal']['id']}, based on release {release}")


if __name__ == "__main__":
    main()
