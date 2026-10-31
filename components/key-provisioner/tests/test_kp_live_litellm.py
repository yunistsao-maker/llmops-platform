"""Against a real LiteLLM proxy (opt-in): LITELLM_TEST_URL and LITELLM_TEST_MASTER_KEY, e.g.

    docker run --rm -p 14013:4000 -e LITELLM_MASTER_KEY=sk-test \
      -e DATABASE_URL=postgresql://postgres:test@host.docker.internal:55432/litellm_test \
      --add-host=host.docker.internal:host-gateway ghcr.io/berriai/litellm-non_root:v1.105.2 --port 4000
"""
import json
import os
import uuid

import httpx
import pytest

from key_provisioner.litellm import LiteLLMAdmin
from key_provisioner.provisioner import Approved, Provisioner
from key_provisioner.settings import Settings
from key_provisioner.sinks import FileSink

URL = os.environ.get("LITELLM_TEST_URL")
MASTER = os.environ.get("LITELLM_TEST_MASTER_KEY")
pytestmark = pytest.mark.skipif(not (URL and MASTER), reason="LITELLM_TEST_URL / LITELLM_TEST_MASTER_KEY not set")


def test_real_litellm_key_lifecycle(tmp_path):
    admin = LiteLLMAdmin(URL, MASTER)
    p = Provisioner(Settings(litellm_url=URL, secret_sink="file"), admin, FileSink(tmp_path))
    rid = str(uuid.uuid4())
    req = Approved.parse({"request_id": rid, "team": "live-test", "alias": "tester", "models": ["claude-haiku-5-5"],
                          "stage": "trial", "budget_usd": 25.0, "rpm": 10, "tpm": 10000, "duration": "14d"})
    try:
        first = p.provision(req)
        assert first.key_alias == f"req-{rid.replace('-', '')[:8]}-trial" and first.expires_at
        info = admin.find_key(first.key_alias)
        assert info["metadata"]["request_id"] == rid and info["max_budget"] == 25.0
        assert info["rpm_limit"] == 10 and info["tpm_limit"] == 10000 and info["models"] == ["claude-haiku-5-5"]
        secret = json.loads(open(first.secret_ref.removeprefix("file:")).read())
        me = httpx.get(f"{URL}/key/info", headers={"Authorization": f"Bearer {secret['api_key']}"}, timeout=10)
        assert me.status_code == 200 and me.json()["info"]["key_alias"] == first.key_alias   # the stored key works
        again = p.provision(req)                                  # idempotent: no second key
        assert again.reused and admin.find_key(first.key_alias)["token"] == info["token"]
        for f in tmp_path.rglob("*.json"):                        # lost secret: key is replaced, still one key
            f.unlink()
        third = p.provision(req)
        assert not third.reused and admin.find_key(first.key_alias)["token"] != info["token"]
    finally:
        for stage_alias in (f"req-{rid.replace('-', '')[:8]}-trial",):
            key = admin.find_key(stage_alias)
            if key:
                admin.delete_key(key["token"])
