"""The agent's port of the portal validator (research/portal/checks.py) must never be weaker than the portal's own.

This needs your real portal repo (its tools/validate_data.py and its data), so it is skipped unless PORTAL_REPO is set:

    PORTAL_REPO=../llmops-portal python -m pytest tests/test_portal_parity.py

Nothing in PORTAL_REPO is modified: every variant is written to a temporary copy of schema/, data/ and tools/.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from research.portal.checks import blocking, validate_docs
from research.portal.data import PortalData

REPO = os.environ.get("PORTAL_REPO")
pytestmark = pytest.mark.skipif(not REPO, reason="set PORTAL_REPO to a checkout of the portal repo (needs tools/validate_data.py)")


def _real_validator(root: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "tools/validate_data.py", "--root", str(root)], cwd=root, capture_output=True, text=True, timeout=300)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


@pytest.fixture()
def copy_of_portal(tmp_path):
    src = Path(REPO).resolve()
    root = tmp_path / "portal"
    for rel in ("schema", "data", "tools"):
        if (src / rel).is_dir():
            shutil.copytree(src / rel, root / rel, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
    assert (root / "tools" / "validate_data.py").is_file(), "PORTAL_REPO has no tools/validate_data.py"
    return root


def _mine(root: Path, told_too: bool = False) -> list[str]:
    """What this validator objects to: the problems that stop a publish (with ``told_too``: also what it only tells -
    data that is usable, in which a value will not do what its author meant)."""
    portal = PortalData.from_dir(root)
    problems = validate_docs(portal.schema, portal.docs)
    return [str(p) for p in (problems if told_too else blocking(problems))]


def test_the_untouched_portal_is_valid_for_both(copy_of_portal):
    rc, out = _real_validator(copy_of_portal)
    assert rc == 0, out
    assert _mine(copy_of_portal) == []
    for told in _mine(copy_of_portal, told_too=True):  # (not a failure: `pytest -rP` shows what a publish would mention)
        print("told, not refused:", told)


# --- known-bad variants: (file, description, mutation of the parsed document; returns False when the file has nothing to mutate) ---------
def _first(doc, key):
    items = doc.get(key) or []
    return items[0] if items and isinstance(items[0], dict) else None


def _impossible_date(doc):
    ev = _first(doc, "events")
    if ev is None:
        return False
    ev["date"] = "2026-02-30"
    return True


def _wrong_type(doc):
    ev = _first(doc, "events")
    if ev is None:
        return False
    ev["date"] = 20261130
    return True


def _extra_property(doc):
    ev = _first(doc, "events")
    if ev is None:
        return False
    ev["bogusField"] = 1
    return True


def _model_extra_property(doc):
    m = _first(doc, "models")
    if m is None:
        return False
    m["bogusField"] = 1
    return True


def _duplicate_event_id(doc):
    ev = _first(doc, "events")
    if ev is None:
        return False
    doc["events"].append(copy.deepcopy(ev))
    return True


def _dangling_source(doc):
    for ev in doc.get("events") or []:
        if ev.get("sources"):
            ev["sources"][0]["id"] = "no-such-source-id"
            return True
    return False


def _missing_required(doc):
    if "schema" not in doc:
        return False
    del doc["schema"]
    return True


def _bad_source_type(doc):
    src = _first(doc, "sources")
    if src is None:
        return False
    src["type"] = "not-a-source-type"
    return True


def _duplicate_model_id(doc):
    m = _first(doc, "models")
    if m is None:
        return False
    doc["models"].append(copy.deepcopy(m))
    return True


def _bad_digest_date(doc):
    if "periodEnd" not in doc:
        return False
    doc["periodEnd"] = "last friday"
    return True


MUTATIONS = [
    ("data/lifecycle.json", _impossible_date), ("data/lifecycle.json", _wrong_type), ("data/lifecycle.json", _extra_property),
    ("data/lifecycle.json", _duplicate_event_id), ("data/lifecycle.json", _dangling_source), ("data/lifecycle.json", _missing_required),
    ("data/sources.json", _bad_source_type), ("data/catalog.json", _duplicate_model_id), ("data/catalog.json", _model_extra_property),
    ("data/digests/*.json", _bad_digest_date),
]


@pytest.mark.parametrize("target, mutate", MUTATIONS, ids=[f"{t}:{m.__name__}" for t, m in MUTATIONS])
def test_everything_the_portal_validator_rejects_is_rejected_by_the_port(copy_of_portal, target, mutate):
    files = sorted(copy_of_portal.glob(target)) if "*" in target else [copy_of_portal / target]
    for path in files:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not mutate(doc):
            continue
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        rc, out = _real_validator(copy_of_portal)
        mine = _mine(copy_of_portal)
        assert rc != 0, f"the portal validator accepted {mutate.__name__} on {path.name}: it is not a bad variant\n{out}"
        assert mine, f"the agent's validator accepted {mutate.__name__} on {path.name} but the portal's rejects it:\n{out}"
        return
    pytest.skip(f"{target} has nothing {mutate.__name__} applies to")


def test_merges_are_matched_the_way_the_radar_matches_titles():
    """``apply.merge_key`` is a port of the portal's ``tools/build_radar.py`` ``norm``: a `merges` title the agent and the
    publisher accept is one the Radar folds into the entry, and one they refuse is one it would not find."""
    import importlib.util

    from research.apply import merge_key

    path = Path(REPO).resolve() / "tools" / "build_radar.py"
    if not path.is_file():
        pytest.skip("PORTAL_REPO has no tools/build_radar.py")
    spec = importlib.util.spec_from_file_location("portal_build_radar", path)
    radar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(radar)  # type: ignore[union-attr]
    texts = ["【10-06 補充】ＧＰＴ-6 Sol／Luna：價格「調降」！", "Azure o 系列統一在 2026-11-19 退役", "  A　B\t—– c…  ", "【2026-10-06 補充：來源更新】x", ""]
    for p in sorted((Path(REPO).resolve() / "data" / "digests").glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        for section in (*radar.SECTIONS, "actionItems"):
            texts += [item.get("title") or item.get("action") or "" for item in doc.get(section, [])]
    assert len(texts) > 5
    assert [merge_key(t) for t in texts] == [radar.norm(t) for t in texts]
