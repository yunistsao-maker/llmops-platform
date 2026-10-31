"""Shared fixtures. Tests against the real llmops-portal data are skipped when that checkout is missing
(set LLMOPS_PORTAL_DIR, default /home/claude/gh/llmops-portal). Database tests need TEST_DATABASE_URL."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/llmops-common/src"))
sys.path.insert(0, str(ROOT / "components/view-builder/src"))
sys.path.insert(0, str(ROOT / "components/db-migrate/src"))

FIXTURE = ROOT / "contracts/fixtures/portal-view.sample.json"
MATRIX = ROOT / "contracts/fixtures/security-matrix.json"
EDITORIAL = ROOT / "components/view-builder/editorial/current.json"
MIGRATIONS = ROOT / "contracts/db/migrations"
PORTAL = Path(os.environ.get("LLMOPS_PORTAL_DIR", "/home/claude/gh/llmops-portal"))
SECURITY_RUN = PORTAL / "runs/security/20261012T151346-a55616.json"


def without_additions(view: dict) -> dict:
    """The view minus what was added after the contract fixture (models[].avail, models[].gw, offs[].zdr, gw)."""
    v = copy.deepcopy(view)
    v.pop("gw", None)
    for m in v["models"]:
        m.pop("avail", None)
        m.pop("gw", None)
        for o in m["offs"].values():
            o.pop("zdr", None)
    return v


@pytest.fixture(scope="session")
def fixture_view():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def portal_data():
    if not (PORTAL / "data/catalog.json").is_file() or not SECURITY_RUN.is_file():
        pytest.skip(f"llmops-portal checkout not found at {PORTAL}")
    return PORTAL / "data"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def mini_data(tmp_path) -> Path:
    """A tiny release in the llmops-portal data/ layout (two models, a few radar cards)."""
    d = tmp_path / "data"
    src = [{"id": "s1", "title": "Model card", "url": "https://example.com/card", "publisher": "Vendor"}]
    _write(d / "sources.json", {"sources": src})
    off = {"cloud": "aws", "service": "bedrock", "status": "ga", "availableSince": "2026-09-01",
           "pricing": [{"scope": "global", "input": 1.0, "output": 5.0}],
           "apacAvailability": [{"region": "ap-east-2", "scopes": ["global"]}],
           "dataProcessing": {"residencyOptions": ["JP geo"], "taiwanResidency": "no"},
           "features": [{"feature": "structured-outputs", "status": "supported"}],
           "lifecycle": {"eolNoSoonerThan": "2027-09-01"}, "litellmModel": "bedrock/x", "sources": [{"id": "s1"}]}
    _write(d / "catalog.json", {"asOf": "2026-10-24", "models": [
        {"id": "alpha-1", "name": "Alpha 1", "vendor": "anthropic", "tier": "frontier", "releaseDate": "2026-09-01",
         "platformStatus": "evaluating", "contextWindow": 200000, "maxOutputTokens": 64000,
         "summary": "Alpha 是測試模型。第二句。", "strengths": ["很快；而且便宜"], "weaknesses": ["只有 AWS"],
         "offerings": [off], "benchmarks": [{"name": "Artificial Analysis Intelligence Index", "score": 50, "rank": "#3 / 10",
                                             "date": "2026-10-01"}], "sources": [{"id": "s1"}]},
        {"id": "beta-2", "name": "Beta 2", "vendor": "openai", "tier": "efficient", "releaseDate": "2026-08-01",
         "platformStatus": "watch", "contextWindow": 128000, "maxOutputTokens": 16000, "summary": "Beta.",
         "offerings": [], "sources": []}]})
    _write(d / "models/alpha-1.json", {"tldr": "Alpha tl;dr", "breakingChanges": [{"title": "改了", "severity": "high"}]})
    card = {"title": "Alpha 1 上架 Bedrock", "date": "2026-10-20", "vendor": "anthropic", "clouds": ["aws"], "impact": "high",
            "detail": "Alpha 1 GA", "sources": [{"id": "s1"}], "id": "it-aaaaaaaaaa", "firstSeen": "2026-10-20",
            "lastSeen": "2026-10-24"}
    old = dict(card, id="it-bbbbbbbbbb", title="舊消息", firstSeen="2026-08-01", lastSeen="2026-08-02", date="2026-08-01")
    _write(d / "radar.json", {"asOf": "2026-10-24", "newModels": [card, old], "pricingChanges": [], "lifecycleChanges": [],
                              "litellm": [], "security": [], "regulatory": [],
                              "actionItems": [{"id": "a1", "priority": "P1", "action": "做某事：細節", "due": "2026-11-01",
                                               "firstSeen": "2026-10-20", "lastSeen": "2026-10-24"}]})
    _write(d / "lifecycle.json", {"events": [
        {"id": "e1", "modelName": "Old Model", "providerModelId": "old-1", "cloud": "aws", "service": "bedrock",
         "event": "retirement", "date": "2026-11-15", "sources": [{"id": "s1"}], "replacement": "alpha-1"},
        {"id": "e2", "modelName": "Gone Model", "providerModelId": "gone", "cloud": "azure", "service": "foundry",
         "event": "retirement", "date": "2026-09-01"}]})
    _write(d / "open-models.json", {"models": [{"id": "o1", "name": "Open 1", "developer": "Lab", "origin": "美國",
                                                "releaseDate": "2026-07-01", "heat": "hit", "weights": {"license": "mit"},
                                                "summary": "開放模型。更多。", "hosting": [{"cloud": "aws", "status": "preview"},
                                                                               {"cloud": "aws", "status": "ga"}]}]})
    _write(d / "requirements.json", {"gates": [{"id": f"G{i}", "name": f"Gate {i}", "owner": "平台", "exitCriteria": ["ok"]}
                                               for i in range(11)]})
    _write(d / "internal/secret.example.json", {"do": "not upload"})
    return d
