import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from llmops_common.auth import AuthError, Authenticator


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


def _token(key, **over):
    now = int(time.time())
    claims = {"iss": "https://idp.example", "aud": "llmops-portal", "sub": "u1", "iat": now, "exp": now + 300,
              "email": "jchen@example.com", "groups": ["llmops-admins", "legal"]}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def test_oidc_valid_token_and_roles(issuer, monkeypatch):
    key, jwks = issuer
    a = Authenticator(mode="oidc", issuer="https://idp.example", audience="llmops-portal", jwks_url=jwks)
    p = a.authenticate("Bearer " + _token(key))
    assert p.sub == "u1" and p.alias == "jchen" and "legal" in p.groups
    monkeypatch.setenv("ROLE_REVIEWER_G2", "legal")
    assert Authenticator.has_role(p, "reviewer-g2")
    assert not Authenticator.has_role(p, "reviewer-g8")


@pytest.mark.parametrize("over", [{"aud": "other"}, {"iss": "https://evil"}, {"exp": int(time.time()) - 3600}])
def test_oidc_rejects_bad_tokens(issuer, over):
    key, jwks = issuer
    a = Authenticator(mode="oidc", issuer="https://idp.example", audience="llmops-portal", jwks_url=jwks)
    with pytest.raises(AuthError):
        a.authenticate("Bearer " + _token(key, **over))
    with pytest.raises(AuthError):
        a.authenticate(None)


def test_dev_mode():
    p = Authenticator(mode="dev", dev_email="me@example.local", dev_groups=("g",)).authenticate(None)
    assert p.email == "me@example.local" and p.groups == ("g",)
