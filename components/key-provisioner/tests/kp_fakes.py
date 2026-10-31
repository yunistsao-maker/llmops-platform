"""In-memory LiteLLM admin API for tests (httpx.MockTransport handler).

Response shapes copied from ghcr.io/berriai/litellm-non_root:v1.105.2: /key/list filters by exact alias and
returns {"keys": [...], "total_count": n}; /key/generate returns the key plus token_id (= sha256 of the key)
and rejects a taken alias with 400; /key/delete answers 404 when nothing matched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets

import httpx

REQUEST_ID = "3f2a9c41-7d1e-4c55-9a0b-2b6f1d9e8c70"


def approved_payload(**over) -> dict:
    """A request.approved payload as request-api publishes it."""
    data = {"request_id": REQUEST_ID, "ref": "req-3f2a9c41", "team": "智慧製造處", "alias": "amy",
            "models": ["claude-sonnet-5-5"], "stage": "trial", "budget_usd": 1733.0, "rpm": 35, "tpm": 122500,
            "duration": "14d", "budget_duration": None, "requester_email": "amy@example.com", "attempt": 1}
    data.update(over)
    return data


class FakeLiteLLM:
    def __init__(self, master_key: str = "sk-master-test"):
        self.master_key = master_key
        self.keys: dict[str, dict] = {}        # token -> record (without the raw key)
        self.issued: dict[str, str] = {}       # token -> raw key (what the test may compare with the secret)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: list[tuple[str, int, dict]] = []   # queued failures: (path, status, json body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def by_alias(self, alias: str) -> dict | None:
        return next((r for r in self.keys.values() if r["key_alias"] == alias), None)

    @staticmethod
    def _error(status: int, message: str, kind: str = "bad_request_error") -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": message, "type": kind, "param": None, "code": str(status)}})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path == "/health/liveliness":
            return httpx.Response(200, text="I'm alive!")
        if request.headers.get("authorization") != f"Bearer {self.master_key}":
            return self._error(401, "Authentication Error, Invalid proxy server token passed.", "token_not_found_in_db")
        if self.fail and self.fail[0][0] == path:
            _, status, payload = self.fail.pop(0)
            return httpx.Response(status, json=payload)
        if path == "/key/list" and request.method == "GET":
            alias = request.url.params.get("key_alias")
            keys = [dict(r) for r in self.keys.values() if alias is None or r["key_alias"] == alias]
            return httpx.Response(200, json={"keys": keys, "total_count": len(keys), "current_page": 1,
                                             "total_pages": 1 if keys else 0})
        if path == "/key/generate" and request.method == "POST":
            alias = body.get("key_alias")
            if alias and self.by_alias(alias):
                return self._error(400, f"Key with alias '{alias}' already exists. Unique key aliases across all keys are required.")
            duration = body.get("duration")
            expires = None
            if duration:
                m = re.match(r"^(\d+)([smhd])$", duration)
                if not m:
                    return self._error(500, f"Unsupported duration unit, passed duration: {duration}", "internal_server_error")
                secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
                expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=secs)).isoformat().replace("+00:00", "Z")
            key = "sk-" + secrets.token_urlsafe(16)
            token = hashlib.sha256(key.encode()).hexdigest()
            record = {"token": token, "key_alias": alias, "models": body.get("models") or [],
                      "max_budget": body.get("max_budget"), "rpm_limit": body.get("rpm_limit"),
                      "tpm_limit": body.get("tpm_limit"), "budget_duration": body.get("budget_duration"),
                      "metadata": body.get("metadata") or {}, "expires": expires, "duration": duration}
            self.keys[token] = record
            self.issued[token] = key
            return httpx.Response(200, json={**record, "key": key, "token_id": token, "key_name": f"sk-...{key[-4:]}"})
        if path == "/key/delete" and request.method == "POST":
            tokens = [t for t in body.get("keys") or [] if t in self.keys]
            tokens += [r["token"] for r in self.keys.values() if r["key_alias"] in (body.get("key_aliases") or [])]
            if not tokens:
                return self._error(404, "{'error': 'No keys found'}", "internal_server_error")
            for t in tokens:
                self.keys.pop(t, None)
            return httpx.Response(200, json={"deleted_keys": tokens})
        return self._error(404, f"no route {path}", "not_found")
