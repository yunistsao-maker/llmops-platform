"""The agent's s3:// locations on any S3-compatible store: S3_ENDPOINT_URL, path-style addressing, static keys.

moto's server plays the self-hosted store on 127.0.0.1 (HTTP, like ``http://object-store:7070`` in the cluster);
nothing here talks to AWS.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from helpers_platform import RELEASE, publish_snapshot
from research import objectstore
from research.agent import run_once
from research.config import Settings
from research.offline.scripted_model import ScriptedModel, smoke_script
from research.portal.data import PortalData
from research.store import read_json_folder, save_result
from research.web.search_backend import FakeSearchBackend


@pytest.fixture()
def s3_endpoint(monkeypatch):
    moto_server = pytest.importorskip("moto.server")
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    for name, value in {"S3_ENDPOINT_URL": endpoint, "S3_REGION": "us-east-1", "S3_ACCESS_KEY_ID": "platform-key",
                        "S3_SECRET_ACCESS_KEY": "platform-secret", "BUCKET_CURATED": "curated-test"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("S3_FORCE_PATH_STYLE", raising=False)
    old = objectstore.set_store(None)  # get_store() builds llmops_common's S3Store from the environment above
    try:
        objectstore.get_store().ensure_bucket("curated-test")
        yield endpoint
    finally:
        objectstore.set_store(old)
        server.stop()


def test_the_store_is_built_from_the_platform_settings(s3_endpoint):
    store = objectstore.get_store()
    client = store.client
    assert client.meta.endpoint_url == s3_endpoint
    assert client.meta.config.s3["addressing_style"] == "path"  # default when S3_ENDPOINT_URL is set
    assert client.meta.region_name == "us-east-1"
    assert objectstore.get_store() is store  # one client per process


def test_results_and_notes_reach_the_endpoint_path_style(s3_endpoint):
    sent: list[str] = []
    objectstore.get_store().client.meta.events.register("before-send.s3.*", lambda request, **_: sent.append(request.url))
    result = {"runId": "run-1", "status": "accepted", "proposal": {"id": "prop-1", "schema": "x"}}
    where = save_result("s3://curated-test/research/", result)
    assert where == "s3://curated-test/research/proposals/prop-1.json"
    # path-style: the bucket is the first segment of the URL path on the configured endpoint, not a host name
    assert sent[:2] == [f"{s3_endpoint}/curated-test/research/runs/run-1.json", f"{s3_endpoint}/curated-test/research/proposals/prop-1.json"]
    assert json.loads(objectstore.get_store().get_bytes("curated-test", "research/runs/run-1.json"))["runId"] == "run-1"
    assert [k for k, _ in read_json_folder("s3://curated-test/research", "proposals", newest=5, max_bytes=10_000)] == ["prop-1.json"]


def test_portal_data_is_read_from_the_endpoint(s3_endpoint):
    publish_snapshot(objectstore.get_store(), "curated-test")
    portal = PortalData.load("s3://curated-test/portal/")
    assert portal.manifest["kind"] == "release" and portal.manifest["releaseId"] == RELEASE
    assert "data/catalog.json" in portal.docs and portal.schema


def test_a_whole_role_run_against_the_endpoint(s3_endpoint):
    publish_snapshot(objectstore.get_store(), "curated-test")
    env = {"BUCKET_CURATED": "curated-test", "MODEL_PROVIDER": "scripted"}
    settings = Settings.from_env(env)
    assert settings.portal_data == "s3://curated-test/portal/" and settings.output_uri == "s3://curated-test/research/"
    res = asyncio.run(run_once({"kind": "focus", "role": "litellm-researcher"}, settings=settings, model=ScriptedModel(smoke_script()),
                               search=FakeSearchBackend({})))
    assert res["status"] == "accepted", res.get("error")
    store = objectstore.get_store()
    keys = sorted(o.key for o in store.list("curated-test", "research/"))
    assert f"research/runs/{res['runId']}.json" in keys and f"research/proposals/{res['proposal']['id']}.json" in keys
    assert any(k.startswith("research/handoff/2026-10-02/") and k.endswith(f"-litellm-researcher-{res['runId']}.json") for k in keys)
    assert res["proposal"]["portal"]["manifest"]["releaseId"] == RELEASE
