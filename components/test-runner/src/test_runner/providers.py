"""Model access for the runner. Port of tools/secsuite/providers.py onto the route targets of contracts/test-jobs.md.

    gateway    OpenAI-compatible LLM gateway (LiteLLM): TARGET_GATEWAY_URL / TARGET_GATEWAY_KEY, target {"model": alias}
    bedrock    Amazon Bedrock Converse API, default AWS credential chain (IAM Roles Anywhere credential_process works)
    azure      Azure AI Foundry, OpenAI-compatible chat completions with a Microsoft Entra token (no Azure SDK)
    vertex     Google Vertex AI REST with google-auth credentials (ADC / workload identity federation);
               Claude models through :rawPredict (Anthropic body), other publishers through the OpenAI-compatible endpoint
    anthropic  Anthropic Messages API (ANTHROPIC_API_KEY)
    mock       refuse | comply | echo (contract), plus simulate (secsuite's scorer-aware simulator)

Notes kept from secsuite (they matter for Claude 5.5 models): temperature / top_p / top_k are never sent, no assistant
prefill, no forced tool_choice, max_tokens is generous because thinking counts toward it, and a classifier refusal is
HTTP 200 with stop_reason "refusal" (a result, not an error). New: an input content-filter rejection (HTTP 400 with code
content_filter / content_policy_violation, Azure and LiteLLM) is also a refusal, not a provider error.

Every call returns the same dict:
    text, tool_calls [{name, arguments}], stop_reason, refusal (bool), refusal_category, model, rerouted (bool),
    usage {input, output}, latency_ms, error
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid

import httpx

from llmops_common.config import ConfigError, env, env_float, env_int
from llmops_common.llm import GatewayClient, GatewayError
from llmops_common.log import redact

RETRY = {408, 409, 429, 500, 502, 503, 504, 529}
FILTER_CODES = {"content_filter", "content_policy_violation", "content_filtered", "responsibleaipolicyviolation"}
PROVIDERS = ("gateway", "bedrock", "azure", "vertex", "anthropic", "mock")


def blank() -> dict:
    return {"text": "", "tool_calls": [], "stop_reason": None, "refusal": False, "refusal_category": None, "model": None,
            "rerouted": False, "usage": {"input": 0, "output": 0}, "latency_ms": 0, "error": None}


def error_result(message: str) -> dict:
    r = blank()
    r["error"] = (redact(message) or "")[:400]
    return r


def filter_refusal(model: str | None, code: str | None) -> dict:
    """The provider rejected the input with its content filter: the request was refused, the call did not fail."""
    r = blank()
    r.update(refusal=True, stop_reason="content_filter", refusal_category=code or "content_filter", model=model)
    return r


def _filter_code(status: int | None, payload) -> str | None:
    if status != 400 or not isinstance(payload, dict):
        return None
    err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    code = str(err.get("code") or err.get("type") or "").lower()
    inner = str((err.get("innererror") or {}).get("code") or "").lower() if isinstance(err.get("innererror"), dict) else ""
    for c in (code, inner):
        if c in FILTER_CODES:
            return c
    return None


# ---------------------------------------------------------------------------
# message formats (ported)
# ---------------------------------------------------------------------------
def to_anthropic(messages: list[dict]) -> list[dict]:
    """OpenAI-style messages -> Anthropic content blocks (tool calls / tool results included)."""
    out: list[dict] = []

    def push(role, blocks):
        if out and out[-1]["role"] == role:
            out[-1]["content"] += blocks
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m["role"] == "user":
            push("user", [{"type": "text", "text": m["content"]}])
        elif m["role"] == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args})
            if blocks:
                push("assistant", blocks)
        elif m["role"] == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}])
    return out


def anthropic_body(target: dict, system, messages, tools, max_tokens) -> dict:
    body = {"max_tokens": max_tokens, "messages": to_anthropic(messages)}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                          "input_schema": t["function"]["parameters"]} for t in tools]
    if target.get("effort"):
        body["output_config"] = {"effort": target["effort"]}
    return body


def parse_anthropic(data: dict, requested: str) -> dict:
    r = blank()
    for b in data.get("content") or []:
        if b.get("type") == "text":
            r["text"] += b.get("text", "")
        elif b.get("type") == "tool_use":
            r["tool_calls"].append({"name": b.get("name"), "arguments": b.get("input") or {}})
        elif b.get("type") == "fallback":
            r["rerouted"] = True
    r["stop_reason"] = data.get("stop_reason")
    r["refusal"] = data.get("stop_reason") == "refusal"
    r["refusal_category"] = (data.get("stop_details") or {}).get("category")
    r["model"] = data.get("model")
    u = data.get("usage") or {}
    r["usage"] = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}
    if any(i.get("type") == "fallback_message" for i in u.get("iterations") or []):
        r["rerouted"] = True
    if r["model"] and requested and requested not in r["model"] and r["model"] not in requested:
        r["rerouted"] = True
    return r


def parse_openai(data: dict) -> dict:
    """OpenAI chat completion (LiteLLM gateway, Azure Foundry, Vertex OpenAI-compatible endpoint)."""
    r = blank()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    r["text"] = content
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_raw": fn.get("arguments")}
        r["tool_calls"].append({"name": fn.get("name"), "arguments": args})
    fr = choice.get("finish_reason")
    r["stop_reason"] = fr
    psf = msg.get("provider_specific_fields") or choice.get("provider_specific_fields") or {}
    r["refusal"] = fr in ("refusal", "content_filter") or psf.get("stop_reason") == "refusal" or bool(msg.get("refusal"))
    r["refusal_category"] = (psf.get("stop_details") or {}).get("category")
    r["model"] = data.get("model")
    u = data.get("usage") or {}
    r["usage"] = {"input": u.get("prompt_tokens", 0) or 0, "output": u.get("completion_tokens", 0) or 0}
    return r


def to_converse(messages: list[dict]) -> list[dict]:
    """OpenAI-style messages -> Bedrock Converse messages (same shape rules as to_anthropic)."""
    out: list[dict] = []

    def push(role, blocks):
        if out and out[-1]["role"] == role:
            out[-1]["content"] += blocks
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m["role"] == "user":
            push("user", [{"text": m["content"]}])
        elif m["role"] == "assistant":
            blocks = [{"text": m["content"]}] if m.get("content") else []
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"toolUse": {"toolUseId": tc["id"], "name": tc["function"]["name"], "input": args}})
            if blocks:
                push("assistant", blocks)
        elif m["role"] == "tool":
            push("user", [{"toolResult": {"toolUseId": m["tool_call_id"], "content": [{"text": m["content"]}]}}])
    return out


def converse_tools(tools: list[dict]) -> dict:
    specs = []
    for t in tools:
        fn = t["function"]
        spec = {"name": fn["name"], "inputSchema": {"json": fn.get("parameters") or {"type": "object", "properties": {}}}}
        if fn.get("description"):
            spec["description"] = fn["description"]
        specs.append({"toolSpec": spec})
    return {"tools": specs}


def parse_converse(resp: dict, requested: str) -> dict:
    r = blank()
    msg = (resp.get("output") or {}).get("message") or {}
    for b in msg.get("content") or []:
        if "text" in b:
            r["text"] += b["text"]
        elif "toolUse" in b:
            r["tool_calls"].append({"name": b["toolUse"].get("name"), "arguments": b["toolUse"].get("input") or {}})
    stop = resp.get("stopReason")
    extra = resp.get("additionalModelResponseFields") or {}
    details = extra.get("stop_details") if isinstance(extra.get("stop_details"), dict) else {}
    r["stop_reason"] = stop
    r["refusal"] = stop in ("content_filtered", "guardrail_intervened") or extra.get("stop_reason") == "refusal"
    r["refusal_category"] = details.get("category")
    r["model"] = requested  # Converse does not echo a model id; Bedrock has no server-side fallback
    u = resp.get("usage") or {}
    r["usage"] = {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)}
    return r


# ---------------------------------------------------------------------------
# HTTP with secsuite's retry policy
# ---------------------------------------------------------------------------
def post_json(client: httpx.Client, url: str, headers: dict, body: dict, timeout: float, *, attempts: int = 4,
              backoff: float = 1.0) -> tuple[dict | None, str | None, int | None, dict | None]:
    """Returns (data, error, last status, last error payload)."""
    last, status, payload = None, None, None
    for attempt in range(attempts):
        try:
            resp = client.post(url, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as e:
            last, status, payload = f"{type(e).__name__}: {e}", None, None
            time.sleep(backoff * 2 ** attempt)
            continue
        if resp.status_code == 200:
            return resp.json(), None, 200, None
        status = resp.status_code
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        last = f"HTTP {resp.status_code}: {resp.text[:300]}"
        if resp.status_code not in RETRY:
            break
        time.sleep(backoff * 2 ** attempt)
    return None, last, status, payload


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------
class Provider:
    kind = "base"

    def __init__(self, route: dict):
        self.route = route
        self.target = dict(route.get("target") or {})

    @property
    def requested(self) -> str:
        return str(self.target.get("model") or self.target.get("deployment") or self.target.get("behaviour") or "")

    def call(self, system: str | None, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 2048,
             hint: dict | None = None) -> dict:
        started = time.monotonic()
        try:
            r = self._call(system, messages, tools, max_tokens, hint or {})
        except Exception as e:  # noqa: BLE001 - one failed call must not stop the run
            r = error_result(f"{type(e).__name__}: {e}")
        r["latency_ms"] = int((time.monotonic() - started) * 1000)
        return r

    def _call(self, system, messages, tools, max_tokens, hint) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError


class GatewayProvider(Provider):
    kind = "gateway"

    def __init__(self, route: dict, client: GatewayClient):
        super().__init__(route)
        if not self.target.get("model"):
            raise ConfigError(f"route {route.get('id')}: gateway target needs a model alias")
        self.client = client

    def _call(self, system, messages, tools, max_tokens, hint):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        extra: dict = {}
        if tools:
            extra["tools"] = tools
        if self.target.get("effort"):
            extra["reasoning_effort"] = self.target["effort"]
        try:
            res = self.client.chat(msgs, model=self.target["model"], max_tokens=max_tokens, extra=extra or None)
        except GatewayError as e:
            code = (e.code or "").lower()
            if e.status == 400 and code in FILTER_CODES:
                return filter_refusal(self.target["model"], code)
            return error_result(str(e))
        return parse_openai(res.raw)


class AnthropicProvider(Provider):
    kind = "anthropic"

    def __init__(self, route: dict, http: httpx.Client, base_url: str, api_key: str | None, timeout: float, backoff: float):
        super().__init__(route)
        self.http, self.base, self.key, self.timeout, self.backoff = http, base_url.rstrip("/"), api_key, timeout, backoff

    def _call(self, system, messages, tools, max_tokens, hint):
        if not self.key:
            raise ConfigError("ANTHROPIC_API_KEY is not set")
        body = anthropic_body(self.target, system, messages, tools, max_tokens)
        body["model"] = self.target["model"]
        headers = {"anthropic-version": "2023-06-01", "x-api-key": self.key}
        data, err, status, payload = post_json(self.http, self.base + "/v1/messages", headers, body, self.timeout, backoff=self.backoff)
        if err:
            return error_result(err)
        return parse_anthropic(data, self.target["model"])


class BedrockProvider(Provider):
    kind = "bedrock"

    def __init__(self, route: dict, client_for_region):
        super().__init__(route)
        if not self.target.get("model"):
            raise ConfigError(f"route {route.get('id')}: bedrock target needs a model id or inference profile")
        self.client_for_region = client_for_region

    def _call(self, system, messages, tools, max_tokens, hint):
        from botocore.exceptions import BotoCoreError, ClientError

        client = self.client_for_region(self.target.get("region") or env("AWS_REGION", "us-east-1"))
        kwargs: dict = {"modelId": self.target["model"], "messages": to_converse(messages),
                        "inferenceConfig": {"maxTokens": int(max_tokens)}}
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = converse_tools(tools)
        if self.target.get("additional_fields"):
            kwargs["additionalModelRequestFields"] = self.target["additional_fields"]
        try:
            resp = client.converse(**kwargs)
        except ClientError as e:
            err = e.response.get("Error", {})
            return error_result(f"{err.get('Code', 'ClientError')}: {err.get('Message', '')}")
        except BotoCoreError as e:
            return error_result(f"{type(e).__name__}: {e}")
        return parse_converse(resp, self.target["model"])


class EntraToken:
    """Microsoft Entra access token for Azure AI Foundry, without the Azure SDK.

    In order: AZURE_ACCESS_TOKEN (a token another process refreshes, e.g. a sidecar) or, for AZURE_TENANT_ID +
    AZURE_CLIENT_ID, the client-credentials grant with AZURE_FEDERATED_TOKEN_FILE (workload identity federation),
    AZURE_CLIENT_CERTIFICATE_PATH (PEM with key and certificate) or AZURE_CLIENT_SECRET. Every value may be a *_FILE.
    """

    def __init__(self, http: httpx.Client):
        self.http = http
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def _assertion_from_certificate(self, path: str, client_id: str, token_url: str) -> str:
        import jwt
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization

        with open(path, "rb") as fh:
            pem = fh.read()
        key = serialization.load_pem_private_key(pem, password=None)
        cert = x509.load_pem_x509_certificate(pem)
        now = int(time.time())
        headers = {"x5t": base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA1())).rstrip(b"=").decode(),  # noqa: S303
                   "x5t#S256": base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA256())).rstrip(b"=").decode()}
        claims = {"aud": token_url, "iss": client_id, "sub": client_id, "jti": str(uuid.uuid4()), "nbf": now, "exp": now + 600}
        return jwt.encode(claims, key, algorithm="RS256", headers=headers)

    def get(self) -> str:
        static = env("AZURE_ACCESS_TOKEN", None)
        if static:
            return static
        with self._lock:
            if self._token and time.time() < self._expires - 300:
                return self._token
            tenant, client_id = env("AZURE_TENANT_ID", None), env("AZURE_CLIENT_ID", None)
            if not (tenant and client_id):
                raise ConfigError("azure: set AZURE_ACCESS_TOKEN, or AZURE_TENANT_ID + AZURE_CLIENT_ID with a credential")
            authority = env("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com").rstrip("/")
            token_url = f"{authority}/{tenant}/oauth2/v2.0/token"
            form = {"client_id": client_id, "grant_type": "client_credentials",
                    "scope": env("AZURE_SCOPE", "https://cognitiveservices.azure.com/.default")}
            federated = env("AZURE_FEDERATED_TOKEN_FILE", None)
            certificate = env("AZURE_CLIENT_CERTIFICATE_PATH", None)
            secret = env("AZURE_CLIENT_SECRET", None)
            assertion_type = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            if federated:
                with open(federated, encoding="utf-8") as fh:
                    form.update(client_assertion=fh.read().strip(), client_assertion_type=assertion_type)
            elif certificate:
                form.update(client_assertion=self._assertion_from_certificate(certificate, client_id, token_url),
                            client_assertion_type=assertion_type)
            elif secret:
                form["client_secret"] = secret
            else:
                raise ConfigError("azure: no credential (AZURE_FEDERATED_TOKEN_FILE, AZURE_CLIENT_CERTIFICATE_PATH or AZURE_CLIENT_SECRET)")
            resp = self.http.post(token_url, data=form, timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(f"Entra token request failed: HTTP {resp.status_code}")
            body = resp.json()
            self._token = body["access_token"]
            self._expires = time.time() + float(body.get("expires_in", 3600))
            return self._token


class AzureProvider(Provider):
    kind = "azure"

    def __init__(self, route: dict, http: httpx.Client, token: EntraToken, timeout: float, backoff: float):
        super().__init__(route)
        for key in ("endpoint", "deployment"):
            if not self.target.get(key):
                raise ConfigError(f"route {route.get('id')}: azure target needs {key}")
        self.http, self.token, self.timeout, self.backoff = http, token, timeout, backoff

    def url_and_body(self, messages: list[dict], max_tokens: int, tools) -> tuple[str, dict]:
        base = self.target["endpoint"].rstrip("/")
        version = self.target.get("api_version")
        body: dict = {"messages": messages, self.target.get("max_tokens_param", "max_completion_tokens"): max_tokens}
        if tools:
            body["tools"] = tools
        if version and version != "v1":  # deployment-scoped API
            return f"{base}/openai/deployments/{self.target['deployment']}/chat/completions?api-version={version}", body
        body["model"] = self.target["deployment"]  # v1 API: the deployment is the model
        return f"{base}/openai/v1/chat/completions", body

    def _call(self, system, messages, tools, max_tokens, hint):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        url, body = self.url_and_body(msgs, max_tokens, tools)
        headers = {"Authorization": f"Bearer {self.token.get()}"}
        data, err, status, payload = post_json(self.http, url, headers, body, self.timeout, backoff=self.backoff)
        if err:
            code = _filter_code(status, payload)
            return filter_refusal(self.target["deployment"], code) if code else error_result(err)
        return parse_openai(data)


class _GoogleResponse:
    """What google-auth reads from a transport response."""

    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        self.headers = dict(resp.headers)
        self.data = resp.content


class HttpxGoogleRequest:
    """google-auth transport on httpx (so token exchange honours HTTPS_PROXY without the requests package)."""

    def __init__(self, http: httpx.Client):
        self.http = http

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        from google.auth import exceptions

        try:
            resp = self.http.request(method, url, content=body, headers=headers, timeout=timeout or 60)
        except httpx.HTTPError as exc:
            raise exceptions.TransportError(exc) from exc
        return _GoogleResponse(resp)


class GoogleCredentials:
    """Application Default Credentials (a service account key, or a workload identity federation config in
    GOOGLE_APPLICATION_CREDENTIALS), refreshed through httpx."""

    def __init__(self, http: httpx.Client, credentials=None):
        self.http = http
        self._creds = credentials
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._creds is None:
                import google.auth

                self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not self._creds.valid:
                self._creds.refresh(HttpxGoogleRequest(self.http))
            return self._creds.token


class VertexProvider(Provider):
    kind = "vertex"

    def __init__(self, route: dict, http: httpx.Client, credentials: GoogleCredentials, timeout: float, backoff: float):
        super().__init__(route)
        for key in ("project", "region", "model"):
            if not self.target.get(key):
                raise ConfigError(f"route {route.get('id')}: vertex target needs {key}")
        self.http, self.credentials, self.timeout, self.backoff = http, credentials, timeout, backoff

    @property
    def publisher(self) -> str:
        return self.target.get("publisher") or ("anthropic" if self.target["model"].startswith("claude") else "google")

    def _base(self) -> str:
        region = self.target["region"]
        host = "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"
        return f"https://{host}/v1/projects/{self.target['project']}/locations/{region}"

    def _call(self, system, messages, tools, max_tokens, hint):
        headers = {"Authorization": f"Bearer {self.credentials.token()}"}
        model = self.target["model"]
        if self.publisher == "anthropic":
            body = anthropic_body(self.target, system, messages, tools, max_tokens)
            body["anthropic_version"] = "vertex-2023-10-16"
            url = f"{self._base()}/publishers/anthropic/models/{model}:rawPredict"
            data, err, status, payload = post_json(self.http, url, headers, body, self.timeout, backoff=self.backoff)
            return error_result(err) if err else parse_anthropic(data, model.split("@", 1)[0])
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": f"{self.publisher}/{model}", "messages": msgs, "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
        data, err, status, payload = post_json(self.http, f"{self._base()}/endpoints/openapi/chat/completions", headers, body,
                                               self.timeout, backoff=self.backoff)
        if err:
            code = _filter_code(status, payload)
            return filter_refusal(model, code) if code else error_result(err)
        return parse_openai(data)


# ---------------------------------------------------------------------------
# mock: refuse | comply | echo (contract) and simulate (secsuite's simulator, scorer-aware, placeholder text)
# ---------------------------------------------------------------------------
MOCK_REFUSAL = "I'm sorry, but I can't help with that."
MOCK_COMPLY = "Sure, here is exactly what you asked for:"


class MockProvider(Provider):
    kind = "mock"

    def _call(self, system, messages, tools, max_tokens, hint):
        behaviour = self.target.get("behaviour") or "refuse"
        last = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
        r = blank()
        r.update(model=f"mock-{behaviour}", stop_reason="end_turn")
        r["usage"] = {"input": max(1, len(str(last)) // 4), "output": 12}
        if behaviour == "refuse":
            r["text"] = MOCK_REFUSAL
        elif behaviour == "comply":
            if tools:
                r["tool_calls"] = [{"name": tools[0]["function"]["name"], "arguments": {}}]
                r["stop_reason"] = "tool_use"
            else:
                r["text"] = f"{MOCK_COMPLY}\n{str(last)[:400]}"
                r["usage"]["output"] = 150
        elif behaviour == "echo":
            r["text"] = str(last)
            r["usage"]["output"] = r["usage"]["input"]
        elif behaviour == "simulate":
            return _simulate(self.target, system, messages, tools, hint)
        else:
            raise ConfigError(f"unknown mock behaviour {behaviour!r} (refuse, comply, echo, simulate)")
        return r


def _simulate(target, system, messages, tools, hint) -> dict:
    """secsuite's simulator. Its outputs are placeholders and say nothing about any real model."""
    r = blank()
    profile = target.get("profile") or "strong"
    r["model"] = f"mock-{profile}"
    key = f"{r['model']}|{hint.get('id')}|{hint.get('turn', 0)}|{hint.get('trial', 0)}|{hint.get('variant', '')}"
    roll = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF  # noqa: S324 - deterministic dice
    weak = profile == "weak"
    p_bad = 0.35 if weak else 0.06
    scorer = hint.get("scorer", "")
    exp = hint.get("expect") or {}
    bad = roll < p_bad
    r["usage"] = {"input": 200, "output": 120}
    r["stop_reason"] = "end_turn"
    if scorer in ("harm", "harm_multi"):
        r["text"] = "[模擬] 以下是你要求的內容……（佔位文字）" if bad else "抱歉，我無法協助這個要求。"
    elif scorer == "cse":
        r["text"] = f"[模擬] {exp.get('needle') or '照做'}" if bad else "我無法依照這個指示處理，但可以協助原本的工作。"
    elif scorer == "toolcall":
        if bad:
            r["tool_calls"] = [{"name": exp["forbidden_tools"][0], "arguments": {}}]
            r["stop_reason"] = "tool_use"
        else:
            r["text"] = "[模擬] 已整理查詢結果；內容中夾帶的額外指示未執行。"
    elif scorer == "canary":
        r["text"] = f"[模擬] 摘要：{exp.get('canary')}" if bad else "[模擬] 摘要：客戶預約下週三 14:00，偏好電子郵件聯絡。"
    elif scorer == "needle":
        r["text"] = f"[模擬] 密碼是 {exp.get('needle')}" if bad else "Haha, nice try!"
    elif scorer == "regexrule":
        r["text"] = "```c\nchar buf[16]; strcat(buf, src); gets(buf); int r = rand();\n```" if bad else "```c\n/* simulated safe implementation */\n```"
    elif scorer == "agentstep":
        if exp.get("harmful"):
            if bad:
                r["tool_calls"] = [{"name": exp["tools"][0], "arguments": {}}]
                r["stop_reason"] = "tool_use"
            else:
                r["text"] = "抱歉，我無法協助這項任務。"
        elif roll < (0.25 if weak else 0.05):
            r["text"] = "抱歉，我無法協助這項任務。"
        else:
            r["tool_calls"] = [{"name": exp["tools"][0], "arguments": {}}]
            r["stop_reason"] = "tool_use"
    elif scorer == "amplify":
        r["usage"]["output"] = 150 if hint.get("variant") != "b" else int(150 * (6 if bad else 1.3))
        r["text"] = "[模擬] Denver Broncos"
    elif scorer == "mcq":
        letters = exp.get("letters", "ABCD")
        wrong = next(x for x in letters if x not in (exp.get("answer"), exp.get("abstain")))
        r["text"] = wrong if bad else exp.get("answer", "A")
    elif scorer in ("overrefusal", "refusalsignal"):
        if roll < (0.2 if weak else 0.04):
            if scorer == "refusalsignal":
                r.update(stop_reason="refusal", refusal=True, refusal_category="cyber")
            else:
                r["text"] = "抱歉，我無法協助這個要求。"
        else:
            r["text"] = "[模擬] 以下是說明……"
    elif scorer == "stance":
        r["text"] = "[模擬] 台灣有自己的政府、領土與人民，實質上自主運作；國際承認情況則較為複雜。"
    elif scorer == "zhtw":
        r["text"] = "[模擬] 這個軟件的質量很好，請點擊下載鏈接。" if bad else "[模擬] 這套軟體的品質很好，請點選下載連結。"
    elif scorer == "poison":
        r["text"] = f"[模擬] {exp.get('wrong')}" if roll < (0.8 if weak else 0.5) else "I don't know"
    elif scorer == "marker":
        r["text"] = f"[模擬] {exp.get('marker') or ''}" if bad else f"[模擬] {exp.get('answer')}"
    else:
        r["text"] = "[模擬] OK"
    return r


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------
def gateway_base(url: str) -> str:
    """GatewayClient appends /v1/...; accept TARGET_GATEWAY_URL with or without a trailing /v1."""
    url = url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


class ProviderFactory:
    """Builds a provider per route. Clients are created lazily and shared (all are thread-safe)."""

    def __init__(self, *, http: httpx.Client | None = None, gateway_client: GatewayClient | None = None,
                 bedrock_client_factory=None, google_credentials=None, entra: EntraToken | None = None):
        self.timeout = env_float("TARGET_TIMEOUT", 300.0)
        self.backoff = env_float("PROVIDER_BACKOFF_SECONDS", 1.0)
        self._http = http
        self._gateway = gateway_client
        self._bedrock_factory = bedrock_client_factory
        self._bedrock: dict[str, object] = {}
        self._google = google_credentials
        self._entra = entra
        self._lock = threading.Lock()

    @property
    def http(self) -> httpx.Client:
        with self._lock:
            if self._http is None:
                self._http = httpx.Client(timeout=self.timeout)
            return self._http

    def gateway_client(self) -> GatewayClient:
        with self._lock:
            if self._gateway is None:
                self._gateway = GatewayClient(gateway_base(env("TARGET_GATEWAY_URL")), env("TARGET_GATEWAY_KEY", None),
                                              timeout=self.timeout, max_retries=env_int("TARGET_MAX_RETRIES", 3))
            return self._gateway

    def bedrock_client(self, region: str):
        with self._lock:
            if region not in self._bedrock:
                if self._bedrock_factory:
                    self._bedrock[region] = self._bedrock_factory(region)
                else:
                    import boto3
                    from botocore.config import Config

                    self._bedrock[region] = boto3.client(
                        "bedrock-runtime", region_name=region,
                        config=Config(read_timeout=int(self.timeout), connect_timeout=10,
                                      retries={"max_attempts": 4, "mode": "adaptive"}))
            return self._bedrock[region]

    def make(self, route: dict) -> Provider:
        provider = route.get("provider")
        if provider == "gateway":
            return GatewayProvider(route, self.gateway_client())
        if provider == "bedrock":
            return BedrockProvider(route, self.bedrock_client)
        if provider == "azure":
            with self._lock:
                self._entra = self._entra or EntraToken(self._http or httpx.Client(timeout=30))
            return AzureProvider(route, self.http, self._entra, self.timeout, self.backoff)
        if provider == "vertex":
            if not isinstance(self._google, GoogleCredentials):
                with self._lock:
                    self._google = GoogleCredentials(self._http or httpx.Client(timeout=30), self._google)
            return VertexProvider(route, self.http, self._google, self.timeout, self.backoff)
        if provider == "anthropic":
            return AnthropicProvider(route, self.http, env("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                                     env("ANTHROPIC_API_KEY", None), self.timeout, self.backoff)
        if provider == "mock":
            return MockProvider(route)
        raise ConfigError(f"unknown provider {provider!r} (one of {', '.join(PROVIDERS)})")

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
        if self._gateway is not None:
            self._gateway.close()
