"""Real OIDC verification (RS256 tokens checked against a local JWKS server), no dependency overrides."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from ra_support import valid_request
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def issuer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", use="sig", alg="RS256")
    body = json.dumps({"keys": [jwk]}).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield key, f"http://127.0.0.1:{srv.server_port}/jwks"
    srv.shutdown()


def _bearer(key, sub, email, groups, **over):
    now = int(time.time())
    claims = {"iss": "https://idp.example/realms/corp", "aud": "llmops-portal", "sub": sub, "iat": now, "exp": now + 300,
              "email": email, "groups": groups}
    claims.update(over)
    return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})}


def test_oidc_tokens_drive_roles_and_ownership(issuer, db, monkeypatch):
    from request_api.app import create_app

    key, jwks = issuer
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example/realms/corp")
    monkeypatch.setenv("OIDC_AUDIENCE", "llmops-portal")
    monkeypatch.setenv("OIDC_JWKS_URL", jwks)
    monkeypatch.setenv("ROLE_REVIEWER_G2", "legal-reviewers")
    monkeypatch.setenv("ROLE_ADMIN", "llmops-admins")
    monkeypatch.setenv("REQUEST_API_WORKER", "false")
    monkeypatch.delenv("ALLOW_SELF_APPROVAL", raising=False)
    client = TestClient(create_app())
    amy = _bearer(key, "sub-amy", "amy.lin@example.com", ["staff"])
    legal = _bearer(key, "sub-legal", "jchen@example.com", ["legal-reviewers"])

    assert client.post("/api/requests", json=valid_request()).status_code == 401
    forged = _bearer(key, "sub-amy", "amy.lin@example.com", ["llmops-admins"], aud="someone-else")
    assert client.get("/api/requests", headers=forged).status_code == 401
    expired = _bearer(key, "sub-amy", "amy.lin@example.com", ["staff"], exp=int(time.time()) - 3600)
    assert client.get("/api/requests?mine=1", headers=expired).status_code == 401

    r = client.post("/api/requests", json=valid_request(data_class="sec-b"), headers=amy)
    assert r.status_code == 201
    rid = r.json()["id"]
    mine = client.get("/api/requests?mine=1", headers=amy).json()["items"]
    assert [(i["id"], i["alias"], i["requester_email"]) for i in mine] == [(rid, "amy.lin", "amy.lin@example.com")]
    assert client.get("/api/requests", headers=amy).status_code == 403
    assert client.post(f"/api/requests/{rid}/decisions", json={"gate": "G2", "decision": "approve"},
                       headers=amy).status_code == 403
    r = client.post(f"/api/requests/{rid}/decisions", json={"gate": "G2", "decision": "approve", "comment": "OK"},
                    headers=legal)
    assert r.status_code == 200 and r.json()["status"] == "provisioning"
    detail = client.get(f"/api/requests/{rid}", headers=amy).json()
    assert detail["decisions"][0]["actor"] == "jchen@example.com"
    assert client.post(f"/api/requests/{rid}/withdraw", headers=amy).status_code == 409

    # subscriptions: signed-in users only, each sees only their own rows
    assert client.get("/api/subscriptions").status_code == 401
    assert client.put("/api/subscriptions/gpt-6-luna").status_code == 401
    assert client.put("/api/subscriptions/gpt-6-luna", headers=amy).status_code == 201
    assert client.get("/api/subscriptions", headers=legal).json() == {"items": []}
    assert [i["model_id"] for i in client.get("/api/subscriptions", headers=amy).json()["items"]] == ["gpt-6-luna"]
