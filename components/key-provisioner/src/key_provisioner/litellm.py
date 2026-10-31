"""LiteLLM proxy admin API (checked against ghcr.io/berriai/litellm-non_root:v1.105.2).

    GET  /key/list?key_alias=<alias>&return_full_object=true  -> {"keys": [{token, key_alias, metadata, expires, ...}]}
         (exact alias match; `token` is sha256 of the key)
    POST /key/generate {models, max_budget, rpm_limit, tpm_limit, duration, budget_duration, key_alias, metadata}
         -> {key, token_id, expires, key_alias, ...}; 400 "Key with alias ... already exists" for a taken alias
    POST /key/delete   {"keys": [token]} -> {"deleted_keys": [...]}; 404 when nothing matched
The master key is read through a callable on every call so a rotated Secret file is picked up.
Nothing here logs or returns the generated key except generate(), whose caller stores it.
"""
from __future__ import annotations

from typing import Callable

import httpx

from llmops_common.log import redact

__all__ = ["AdminError", "LiteLLMAdmin"]


class AdminError(Exception):
    def __init__(self, message: str, status: int | None = None, transient: bool = True, alias_taken: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.alias_taken = alias_taken


class LiteLLMAdmin:
    def __init__(self, base_url: str, master_key: Callable[[], str] | str, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self._master_key = master_key if callable(master_key) else (lambda: master_key)
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._master_key()}", "Content-Type": "application/json"}
        try:
            return self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise AdminError(f"gateway admin API unreachable: {type(exc).__name__}") from None

    @staticmethod
    def _error(resp: httpx.Response, what: str) -> AdminError:
        try:
            err = resp.json().get("error", resp.json())
            message = err.get("message") if isinstance(err, dict) else err
        except (ValueError, AttributeError):
            message = resp.text
        message = redact(str(message))[:300]
        status = resp.status_code
        alias_taken = status == 400 and "already exists" in message
        # 401/403: a wrong or rotated master key can be fixed without a code change, so retry for a while.
        transient = alias_taken or status in (401, 403, 408, 409, 425, 429) or status >= 500
        return AdminError(f"{what} failed: HTTP {status}: {message}", status, transient, alias_taken)

    def find_key(self, key_alias: str) -> dict | None:
        resp = self._request("GET", "/key/list", params={"key_alias": key_alias, "return_full_object": "true", "size": 10})
        if resp.status_code != 200:
            raise self._error(resp, "key lookup")
        try:
            keys = resp.json().get("keys") or []
        except (ValueError, AttributeError):
            raise AdminError("key lookup returned no JSON") from None
        for item in keys:
            if isinstance(item, dict) and item.get("key_alias") == key_alias:
                return item
        return None

    def generate(self, body: dict) -> dict:
        resp = self._request("POST", "/key/generate", json=body)
        if resp.status_code != 200:
            raise self._error(resp, "key generation")
        try:
            data = resp.json()
        except ValueError:
            raise AdminError("key generation returned no JSON") from None
        if not isinstance(data, dict) or not data.get("key"):
            raise AdminError("key generation returned no key")
        return data

    def delete_key(self, token: str) -> bool:
        """Delete by hashed token. Returns False when the key was already gone."""
        resp = self._request("POST", "/key/delete", json={"keys": [token]})
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        raise self._error(resp, "key delete")

    def check_access(self) -> None:
        """Master key accepted (used by check-config)."""
        resp = self._request("GET", "/key/list", params={"size": 1})
        if resp.status_code != 200:
            raise self._error(resp, "admin access check")

    def ping(self) -> None:
        resp = self._http.get("/health/liveliness", timeout=5)
        resp.raise_for_status()
