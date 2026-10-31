import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/llmops-common/src"))
sys.path.insert(0, str(ROOT / "components/content-api/src"))

from fastapi.testclient import TestClient  # noqa: E402

from content_api.app import ContentSource, create_app, etag_matches  # noqa: E402
from llmops_common.auth import Authenticator  # noqa: E402
from llmops_common.storage import MemoryStore  # noqa: E402

B = "llmops-curated"
V1 = "20261031T060000Z-aaaaaaaaaaaa"
V2 = "20261031T070000Z-bbbbbbbbbbbb"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _store():
    s = MemoryStore()
    s.put_json(B, f"portal/views/{V1}/portal.json", {"asOf": "2026-10-24", "models": [{"id": "m1", "name": "名稱"}]})
    s.put_json(B, f"portal/views/{V2}/portal.json", {"asOf": "2026-10-25", "models": []})
    s.put_json(B, "portal/views/current.json", {"current": V1, "release_id": "r1", "security_run_id": None,
                                                "built_at": "2026-10-31T06:00:00Z"})
    s.put_json(B, "portal/manifest.json", {"current": "r1", "history": ["r1"]})
    return s


@pytest.fixture()
def env():
    store, clock = _store(), Clock()
    app = create_app(store=store, bucket=B, authenticator=Authenticator(mode="dev"), pointer_ttl=30, clock=clock)
    return TestClient(app), store, clock


def test_current_view_etag_and_304(env):
    client, store, clock = env
    r = client.get("/api/content/portal.json", headers={"accept-encoding": "identity"})
    assert r.status_code == 200 and r.json()["models"][0]["name"] == "名稱"
    assert r.headers["etag"] == f'"{V1}"' and r.headers["cache-control"] == "no-cache"
    assert r.headers["content-type"].startswith("application/json") and "content-encoding" not in r.headers
    r304 = client.get("/api/content/portal.json", headers={"if-none-match": f'W/"{V1}"'})
    assert r304.status_code == 304 and r304.content == b"" and r304.headers["etag"] == f'"{V1}"'
    assert client.get("/api/content/portal.json", headers={"if-none-match": '"other", *'}).status_code == 304

    # the pointer moves; the cached pointer is used until the TTL runs out
    store.put_json(B, "portal/views/current.json", {"current": V2})
    assert client.get("/api/content/portal.json").headers["etag"] == f'"{V1}"'
    clock.t += 31
    r = client.get("/api/content/portal.json", headers={"if-none-match": f'"{V1}"'})
    assert r.status_code == 200 and r.headers["etag"] == f'"{V2}"' and r.json()["asOf"] == "2026-10-25"


def test_gzip_negotiation(env):
    client, _, _ = env
    r = client.get("/api/content/portal.json", headers={"accept-encoding": "gzip, br"})
    assert r.headers["content-encoding"] == "gzip" and r.headers["vary"] == "Accept-Encoding"
    assert r.json()["asOf"] == "2026-10-24"  # the client decodes it transparently
    raw = client.get("/api/content/portal.json", headers={"accept-encoding": "gzip;q=0"})
    assert "content-encoding" not in raw.headers


def test_immutable_view_and_manifest(env):
    client, _, _ = env
    r = client.get(f"/api/content/views/{V2}/portal.json")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"] and r.headers["etag"] == f'"{V2}"'
    assert client.get(f"/api/content/views/{V2}/portal.json", headers={"if-none-match": f'"{V2}"'}).status_code == 304
    assert client.get("/api/content/views/20261031T000000Z-cccccccccccc/portal.json").json()["error"]["code"] == "not_found"
    assert client.get("/api/content/views/..%2F..%2Fsecret/portal.json").status_code == 404
    m = client.get("/api/content/manifest")
    assert m.status_code == 200 and m.json()["current"] == "r1" and m.json()["view"]["current"] == V1


def test_no_view_yet_and_store_down():
    store = MemoryStore()
    client = TestClient(create_app(store=store, bucket=B, authenticator=Authenticator(mode="dev"), pointer_ttl=0))
    r = client.get("/api/content/portal.json")
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_view" and r.headers["retry-after"] == "30"
    assert client.get("/api/content/manifest").status_code == 503
    assert client.get("/readyz").status_code == 200  # reachable, just empty

    class Down(MemoryStore):
        def get_bytes(self, *a):
            raise ConnectionError("down")

        def head(self, *a):
            raise ConnectionError("down")

    client = TestClient(create_app(store=Down(), bucket=B, authenticator=Authenticator(mode="dev"), pointer_ttl=0))
    r = client.get("/api/content/portal.json")
    assert r.status_code == 503 and r.json()["error"]["code"] == "store_unavailable"
    assert client.get("/readyz").status_code == 503 and client.get("/healthz").status_code == 200


def test_stale_pointer_served_while_store_down():
    store, clock = _store(), Clock()
    src = ContentSource(store, B, ttl=30, clock=clock)
    assert src.pointer()["current"] == V1
    store.get_bytes = lambda *a: (_ for _ in ()).throw(ConnectionError("down"))  # type: ignore[assignment]
    clock.t += 60
    assert src.pointer()["current"] == V1


def test_auth_required(monkeypatch):
    store = _store()
    auth = Authenticator(mode="oidc", issuer="https://idp.example", audience="llmops-portal", jwks_url="http://127.0.0.1:9/jwks")
    client = TestClient(create_app(store=store, bucket=B, authenticator=auth))
    r = client.get("/api/content/portal.json")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.get("/healthz").status_code == 200


def test_etag_matches():
    assert etag_matches('"a", W/"b"', '"b"') and not etag_matches('"a"', '"b"') and not etag_matches(None, '"b"')


def test_real_view_roundtrip():
    """The fixture view goes through unchanged (byte for byte, gzip or not)."""
    raw = (ROOT / "contracts/fixtures/portal-view.sample.json").read_bytes()
    store = MemoryStore()
    store.put_bytes(B, f"portal/views/{V1}/portal.json", raw)
    store.put_json(B, "portal/views/current.json", {"current": V1})
    client = TestClient(create_app(store=store, bucket=B, authenticator=Authenticator(mode="dev")))
    r = client.get("/api/content/portal.json", headers={"accept-encoding": "identity"})
    assert r.content == raw
    gz = client.get("/api/content/portal.json", headers={"accept-encoding": "gzip"}, )
    assert json.loads(gz.content) == json.loads(raw)
    assert len(gzip.compress(raw)) < len(raw) / 3
