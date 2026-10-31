"""Everything that leaves the agent: PR text, saved results, S3 locations, replay of a proposal onto a checkout."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from e2e_helpers import FOUNDRY_HTML, FOUNDRY_URL, Site, foundry_script, settings
from research import apply as A
from research.agent import run_once
from research.events import EventMapper, display_text, with_display
from research.offline.scripted_model import ScriptedModel
from research.portal.data import BUNDLED_DIR, PortalData
from research.portal.patch import get_at
from research.render import code, fence, render_pr_body, render_result_text
from research.store import run_location, save_result
from research.web.search_backend import FakeSearchBackend

import asyncio


@pytest.fixture(scope="module")
def accepted():
    site = Site().add(FOUNDRY_URL, FOUNDRY_HTML)
    res = asyncio.run(run_once({"prompt": "check foundry", "kind": "prompt"}, settings=settings(), model=ScriptedModel(foundry_script()),
                               fetcher=site.fetcher(), search=FakeSearchBackend({})))
    assert res["status"] == "accepted"
    return res


# --------------------------------------------------------------------------- PR body
def test_pr_body_has_the_diff_the_evidence_and_the_caveats(accepted):
    body = render_pr_body(accepted["proposal"], accepted)
    assert "## Research agent proposal `prop-" in body
    assert "| 1 | `data/lifecycle.json` | replace | `/events/azure-claude-sonnet-4-5-eol/date` | `2026-11-15` | `2026-11-30` | e1 |" in body
    assert "SAMPLE" in body  # bundled sample data is called out
    assert "claude-sonnet-4-5 | 1 | GA | 2026-11-30 | claude-sonnet-5-5" in body  # the verbatim quote
    assert "https://learn.microsoft.com/" in body and "官方" in body
    assert "platformStatus" in body and "npm run validate" in body


def _outside_code(md: str) -> str:
    """The markdown with fenced blocks and inline code spans removed: what GitHub would actually interpret."""
    import re

    md = re.sub(r"(`{3,})[^\n]*\n.*?\n\1", "", md, flags=re.S)
    return re.sub(r"(`+)(?:(?!\1).)+?\1", "", md)


def test_web_text_cannot_break_out_of_the_pr_markdown(accepted):
    evil = "row | ![x](https://evil.example/p.png?d=1) [click](https://evil.example) <script>alert(1)</script> <img src=//evil.example/a> ``` # heading\n| injected |"
    p = json.loads(json.dumps(accepted["proposal"]))
    p["summary"] = evil
    p["evidence"]["e1"]["quote"] = evil
    p["evidence"]["e1"]["title"] = evil
    p["evidence"]["e1"]["note"] = evil
    p["evidence"]["e1"]["publisher"] = evil
    p["evidence"]["e1"]["url"] = "https://learn.microsoft.com/a b)<img src=x onerror=1>](https://evil.example)"
    p["ops"][0]["rationale"] = evil
    p["ops"][0]["before"] = evil
    p["ops"][0]["value"] = evil
    p["checked"] = [{"topic": evil, "result": evil}]
    p["unverified"] = [{"claim": evil, "reason": evil}]
    p["validation"]["warnings"] = [evil]
    p["newSources"] = [{"id": evil, "title": evil, "url": "https://x.example/" + evil, "type": evil}]
    md = render_pr_body(p)
    body = _outside_code(md)
    # nothing GitHub would interpret survives outside code spans / fences (URLs are percent-encoded, not dropped)
    for needle in ("![", "](", "<script", "<img", "| injected", "\n# heading"):
        assert needle not in body, (needle, body)
    for line in body.splitlines():
        for token in line.split():
            if token.startswith("https://") and ("x.example" in token or "learn.microsoft.com/a" in token):
                assert not set("<>()[]|\"'`\\") & set(token), token
    # the quote itself is still shown, in a fence longer than any backtick run inside it
    assert "````text" in md


def test_code_spans_are_single_line_and_pipe_safe():
    assert code("a|b\nc`d", 50) == "`a¦b c'd`"
    assert code("") == "—"
    assert len(code("x" * 500, 30)) <= 33
    assert fence("a ``` b").startswith("````text")


def test_result_text_covers_every_outcome(accepted):
    assert "通過驗證" in render_result_text(accepted)
    assert "沒有產出" in render_result_text({"status": "no_proposal", "error": "e", "evidence": [1, 2], "runId": "r"})
    assert "執行失敗" in render_result_text({"status": "error", "error": "ThrottlingException", "runId": "r"})


# --------------------------------------------------------------------------- saved results
def test_directory_store_writes_run_and_proposal(tmp_path, accepted):
    loc = save_result(str(tmp_path), accepted)
    assert loc.endswith(f"proposals/{accepted['proposal']['id']}.json")
    assert (tmp_path / "runs" / f"{accepted['runId']}.json").exists()
    assert run_location(str(tmp_path), "r1") == str(tmp_path / "runs" / "r1.json")


def test_nothing_is_written_without_an_output_location(accepted):
    assert save_result("", accepted) is None


def _store(files=None):
    """An llmops_common MemoryStore holding ``files`` in bucket "b" (on the platform every s3:// location goes through
    llmops_common.storage; tests/test_objectstore.py covers a real S3 endpoint)."""
    from llmops_common.storage import MemoryStore

    store = MemoryStore()
    for key, body in (files or {}).items():
        store.put_bytes("b", key, body if isinstance(body, bytes) else body.encode())
    return store


def test_s3_store_uses_the_prefix_and_json_content_type(accepted):
    s3 = _store()
    loc = save_result("s3://my-bucket/research", accepted, store=s3)
    keys = [o.key for o in s3.list("my-bucket")]
    assert keys == sorted([f"research/runs/{accepted['runId']}.json", f"research/proposals/{accepted['proposal']['id']}.json"])
    assert loc == f"s3://my-bucket/research/proposals/{accepted['proposal']['id']}.json"
    assert all(s3._data[("my-bucket", k)][1] == "application/json" for k in keys)
    assert json.loads(s3.get_bytes("my-bucket", f"research/proposals/{accepted['proposal']['id']}.json")) == accepted["proposal"]
    assert run_location("s3://my-bucket/research", "r9") == "s3://my-bucket/research/runs/r9.json"
    assert run_location("s3://my-bucket", "r9") == "s3://my-bucket/runs/r9.json"


def test_results_go_to_the_process_wide_object_store_by_default(accepted, memory_store):
    loc = save_result("s3://llmops-curated/research/", accepted)
    assert loc == f"s3://llmops-curated/research/proposals/{accepted['proposal']['id']}.json"
    assert memory_store.exists("llmops-curated", f"research/runs/{accepted['runId']}.json")


def test_portal_data_can_be_read_from_s3_and_skips_internal_files():
    files = {}
    for path in BUNDLED_DIR.rglob("*.json"):
        files["portal/" + path.relative_to(BUNDLED_DIR).as_posix()] = path.read_bytes()
    files["portal/data/internal/inventory.json"] = b'{"secret": true}'
    files["portal/runs/2026-10-01.json"] = b'{"secret": true}'
    s3 = _store(files)
    portal = PortalData.from_s3("s3://b/portal", store=s3)
    assert portal.manifest["kind"] == "s3"
    assert "data/lifecycle.json" in portal.docs
    assert not any("internal" in k or k.startswith("runs/") for k in portal.docs)
    assert portal.docs["data/lifecycle.json"] == PortalData.load("bundled").docs["data/lifecycle.json"]


def test_a_saved_proposal_cannot_change_the_platform_teams_fields(accepted):
    """A proposal file is data from somewhere else by the time it is replayed: the rule is enforced again, in code."""
    portal = PortalData.load("bundled")
    tampered = json.loads(json.dumps(accepted["proposal"]))
    tampered["ops"].append({"file": "data/catalog.json", "op": "replace", "path": "/models/claude-sonnet-5-5/platformStatus", "value": "ga",
                            "evidence": [], "rationale": "the page said so"})
    staged, problems = A.apply_saved_proposal(portal, tampered)
    assert any(p.startswith("protected field: data/catalog.json: platformStatus of claude-sonnet-5-5") for p in problems), problems
    _staged, clean = A.apply_saved_proposal(portal, accepted["proposal"])
    assert clean == []


# --------------------------------------------------------------------------- portal data published as releases
def _release_files(prefix: str = "portal/", release: str = "20261005T010203Z-abc123", pointer: str = "v1") -> dict:
    """The publisher's layout: <prefix>manifest.json points at <prefix>releases/<id>/.  ``pointer``: "v1" is the AWS
    pipeline's pointer, "platform" the publisher component's ({"current": id, "history": [...]})."""
    files = {f"{prefix}releases/{release}/{path.relative_to(BUNDLED_DIR).as_posix()}": path.read_bytes()
             for path in BUNDLED_DIR.rglob("*.json") if path.name != "snapshot_manifest.json"}
    if pointer == "v1":
        files[f"{prefix}manifest.json"] = json.dumps({
            "schema": "portal-manifest/v1", "releaseId": release, "basePath": f"releases/{release}/",
            "publishedAt": "2026-10-05T01:02:03Z", "digests": ["2026-09-24"]}).encode()
    else:
        files[f"{prefix}manifest.json"] = json.dumps({
            "schema": "portal-manifest/v2", "current": release, "published_at": "2026-10-05T01:02:03Z",
            "history": [{"release_id": release, "published_at": "2026-10-05T01:02:03Z", "kind": "agent", "summary": "新增 Foundry 退役日",
                         "changed": 2},
                        {"release_id": "20261004T000000Z", "published_at": "2026-10-04T00:00:00Z", "kind": "bootstrap", "changed": 40}]}).encode()
    return files


def test_portal_data_on_s3_is_read_from_the_release_the_pointer_names():
    files = _release_files()
    stale = json.loads(files["portal/releases/20261005T010203Z-abc123/data/lifecycle.json"])
    stale["asOf"] = "1999-01-01"  # an older release and a leftover flat copy must both be ignored
    files["portal/releases/20260901T000000Z-old000/data/lifecycle.json"] = json.dumps(stale).encode()
    files["portal/data/lifecycle.json"] = json.dumps(stale).encode()
    files["portal/releases/20261005T010203Z-abc123/data/internal/platform-inventory.json"] = b'{"secret": true}'
    files["portal/releases/20261005T010203Z-abc123/runs/compat/x.json"] = b'{"secret": true}'
    portal = PortalData.from_s3("s3://b/portal/", store=_store(files))
    assert portal.manifest == {"kind": "release", "source": "s3://b/portal/", "releaseId": "20261005T010203Z-abc123",
                               "publishedAt": "2026-10-05T01:02:03Z"}
    assert portal.docs["data/lifecycle.json"]["asOf"] != "1999-01-01"
    assert portal.docs.keys() == PortalData.load("bundled").docs.keys()  # no internal inventory, no test runs
    assert not portal.is_sample


def test_the_platform_pointer_names_the_release_and_its_recent_history():
    files = _release_files(release="20261005T010203Z", pointer="platform")
    files["portal/releases/20261004T000000Z/data/lifecycle.json"] = b'{"asOf": "1999-01-01"}'  # the previous release: not read
    portal = PortalData.from_s3("s3://b/portal/", store=_store(files))
    assert portal.manifest == {"kind": "release", "source": "s3://b/portal/", "releaseId": "20261005T010203Z",
                               "publishedAt": "2026-10-05T01:02:03Z",
                               "recentReleases": [{"releaseId": "20261005T010203Z", "publishedAt": "2026-10-05T01:02:03Z", "kind": "agent",
                                                   "summary": "新增 Foundry 退役日", "changed": 2},
                                                  {"releaseId": "20261004T000000Z", "publishedAt": "2026-10-04T00:00:00Z",
                                                   "kind": "bootstrap", "changed": 40}]}
    assert portal.docs.keys() == PortalData.load("bundled").docs.keys()


@pytest.mark.parametrize("current", ["../x", "a/b", "", None, 7, ".hidden"])
def test_a_platform_pointer_naming_an_unsafe_release_stops_the_run(current):
    files = _release_files(pointer="platform")
    files["portal/manifest.json"] = json.dumps({"current": current, "history": []}).encode()
    with pytest.raises(ValueError, match="invalid release"):
        PortalData.from_s3("s3://b/portal/", store=_store(files))


def test_portal_data_in_a_folder_follows_the_release_pointer_too(tmp_path):
    for key, body in _release_files(prefix="").items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    portal = PortalData.load(str(tmp_path))
    assert portal.manifest["kind"] == "release" and portal.manifest["releaseId"] == "20261005T010203Z-abc123"
    assert portal.docs["data/lifecycle.json"] == PortalData.load("bundled").docs["data/lifecycle.json"]


@pytest.mark.parametrize("base", ["../../etc/", "releases/../x/", "releases/a/b/", "/releases/x/", "releases/x", "", None, 7])
def test_a_release_pointer_with_an_unsafe_path_stops_the_run(base):
    files = _release_files()
    files["portal/manifest.json"] = json.dumps({"schema": "portal-manifest/v1", "releaseId": "x", "basePath": base}).encode()
    with pytest.raises(ValueError, match="invalid release path"):
        PortalData.from_s3("s3://b/portal/", store=_store(files))


def test_a_manifest_that_is_not_a_release_pointer_is_ignored():
    """The UI's own build writes data/manifest.json; a file called manifest.json without the pointer schema changes nothing."""
    files = {"portal/" + path.relative_to(BUNDLED_DIR).as_posix(): path.read_bytes() for path in BUNDLED_DIR.rglob("*.json")}
    files["portal/manifest.json"] = b'{"generatedAt": "2026-10-01T00:00:00Z", "digests": ["2026-09-24"]}'
    portal = PortalData.from_s3("s3://b/portal", store=_store(files))
    assert portal.manifest["kind"] == "s3" and "data/lifecycle.json" in portal.docs


# --------------------------------------------------------------------------- events
def test_event_mapper_drops_reasoning_and_shrinks_tool_payloads():
    m = EventMapper()
    assert m.map({"data": "hello"}) == [{"type": "text", "delta": "hello"}]
    assert m.map({"data": "secret thoughts", "reasoning": True}) == []
    out = m.map({"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": "fetch_page", "input": {"url": "https://x/" + "a" * 500}}}]}})
    assert len(out[0]["input"]["url"]) < 200
    out = m.map({"message": {"content": [{"toolResult": {"toolUseId": "t1", "status": "success",
                                                         "content": [{"text": json.dumps({"pageId": "p1", "outline": ["x"] * 500, "chars": 9})}]}}]}})
    assert out[0]["name"] == "fetch_page" and out[0]["result"] == {"pageId": "p1", "chars": 9}
    assert m.map("not a dict") == []


@pytest.mark.parametrize("manifest, expect", [
    ({"kind": "release", "source": "s3://bucket/portal/releases/20261005T010000Z-3f9a1c2b/", "releaseId": "20261005T010000Z-3f9a1c2b"},
     "portal data: release 20261005T010000Z-3f9a1c2b（s3://bucket/portal/releases/20261005T010000Z-3f9a1c2b/）"),
    ({"kind": "sample"}, "SAMPLE"),
    ({"kind": "portal-checkout", "source": "/repo/llmops-portal"}, "portal data: portal-checkout（/repo/llmops-portal）"),
    (None, "portal data: ?"),
])
def test_the_first_line_of_a_run_says_which_portal_data_it_reads(manifest, expect):
    """A run against the bundled sample looks exactly like a real one otherwise - and its proposal cannot be published."""
    text = display_text({"type": "run_started", "runId": "run-1", "provider": "bedrock", "model": "m", "search": "gateway",
                         "budgets": {"searches": 3, "fetches": 4}, "portal": manifest})
    assert text.startswith("▶ run run-1") and expect in text.splitlines()[1]
    if manifest == {"kind": "sample"}:
        assert "PORTAL_DATA_URI" in text and "不能發布" in text


def test_display_hint_is_added_only_where_there_is_text():
    assert with_display({"type": "heartbeat", "elapsedSeconds": 5}) == {"type": "heartbeat", "elapsedSeconds": 5}
    ev = with_display({"type": "tool_call", "name": "fetch_page", "input": {"url": "https://x"}})
    assert ev["event"]["contentBlockDelta"]["delta"]["text"].startswith("\n→ fetch_page")
    assert display_text({"type": "tool_result", "ok": False, "result": {"error": "nope"}}).startswith("  ✗")


# --------------------------------------------------------------------------- replay onto a checkout
@pytest.fixture()
def checkout(tmp_path):
    root = tmp_path / "portal"
    shutil.copytree(BUNDLED_DIR, root, ignore=shutil.ignore_patterns("snapshot_manifest.json"))
    return root


def test_replay_matches_what_the_agent_validated(accepted, checkout):
    portal = PortalData.from_dir(checkout)
    staged, problems = A.apply_saved_proposal(portal, accepted["proposal"])
    assert problems == []
    assert A.changed_files(portal, staged) == ["data/lifecycle.json", "data/sources.json"]
    assert get_at(staged.docs, "data/lifecycle.json", "/events/azure-claude-sonnet-4-5-eol/date") == "2026-11-30"
    diff = A.diff_text(portal, staged)
    assert '-  "date": "2026-11-15"' in diff or '-      "date": "2026-11-15"' in diff
    assert "+++ b/data/lifecycle.json" in diff


def test_write_changes_produces_the_repo_json_style_and_touches_only_data_files(accepted, checkout):
    portal = PortalData.from_dir(checkout)
    staged, _ = A.apply_saved_proposal(portal, accepted["proposal"])
    before_schema = (checkout / "schema" / "llmops.schema.json").read_bytes()
    written = A.write_changes(checkout, portal, staged)
    assert written == ["data/lifecycle.json", "data/sources.json"]
    text = (checkout / "data" / "lifecycle.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and "Foundry 表" in text  # non-ASCII kept, trailing newline
    assert json.loads(text) == staged.docs["data/lifecycle.json"]
    assert (checkout / "schema" / "llmops.schema.json").read_bytes() == before_schema
    # unchanged files are byte-for-byte what they were
    assert PortalData.from_dir(checkout).docs["data/catalog.json"] == portal.docs["data/catalog.json"]


def test_replay_notices_when_the_portal_moved_on(accepted, checkout):
    portal = PortalData.from_dir(checkout)
    ev = next(e for e in portal.docs["data/lifecycle.json"]["events"] if e["id"] == "azure-claude-sonnet-4-5-eol")
    ev["date"] = "2026-12-01"  # someone already changed it
    _, problems = A.apply_saved_proposal(portal, accepted["proposal"])
    assert any("re-run the research" in p and "2026-12-01" in p for p in problems)


def test_replay_refuses_things_that_are_not_proposals(checkout):
    portal = PortalData.from_dir(checkout)
    _, problems = A.apply_saved_proposal(portal, {"schema": "something-else"})
    assert problems and "not a research proposal" in problems[0]


def test_write_changes_refuses_paths_outside_data(checkout):
    portal = PortalData.from_dir(checkout)
    evil = portal.clone()
    evil.docs["../outside.json"] = {"x": 1}
    with pytest.raises(ValueError):
        A.write_changes(checkout, portal, evil)
    evil = portal.clone()
    evil.docs["schema/llmops.schema.json"] = {"x": 1}
    with pytest.raises(ValueError):
        A.write_changes(checkout, portal, evil)
