"""request.approved -> LiteLLM virtual key -> secret store -> key.provisioned (or key.failed).

Idempotency (a job may run again after a crash, a lost commit or an admin retry):
  1. key alias = <KEY_ALIAS_PREFIX>-<first 8 hex of the request id>-<stage>; if that alias belongs to another
     request (metadata.request_id differs) the long form with all 32 hex digits is used instead.
  2. alias exists in LiteLLM and the secret store holds that same key (token_id match) -> nothing is created;
     key.provisioned is published again (its dedupe key stops a second fan-out).
  3. alias exists but the store does not hold it (crash between generate and store) -> nobody can ever read
     that key, so it is deleted and a new one is generated. There is never more than one key per alias.
The key itself only lives in memory between /key/generate and the secret store write; it is never logged,
published or put in a job.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field

from llmops_common import db, queue
from llmops_common.ids import iso
from llmops_common.log import get_logger, kv, redact
from llmops_common.queue import Job, PermanentError, RetryLater

from .litellm import AdminError, LiteLLMAdmin
from .settings import Settings
from .sinks import SecretSink, SinkError, check_path

QUEUE = "key-provisioner"
SOURCE = "key-provisioner"
STAGES = ("trial", "full")
DURATION_RE = re.compile(r"^[1-9][0-9]{0,4}(s|m|h|d|w|mo)$")   # LiteLLM answers 500 for anything else
log = get_logger("key_provisioner")


class ProvisionError(Exception):
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class Approved:
    """The request.approved payload (contract fields + the documented extras)."""
    request_id: str
    stage: str
    team: str
    alias: str | None = None
    models: tuple[str, ...] = ()
    budget_usd: float | None = None
    rpm: int | None = None
    tpm: int | None = None
    duration: str | None = None
    budget_duration: str | None = None
    requester_email: str | None = None
    ref: str | None = None
    extra: dict = field(default_factory=dict, compare=False)

    @classmethod
    def parse(cls, data: dict) -> "Approved":
        rid = str(data.get("request_id") or "")
        if not rid:
            raise ValueError("request_id missing")
        stage = data.get("stage")
        if stage not in STAGES:
            raise ValueError(f"stage must be trial or full, got {stage!r}")
        models = data.get("models") or []
        if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
            raise ValueError("models must be a list of strings")

        def num(name, kind):
            value = data.get(name)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
            return kind(value)

        for name in ("duration", "budget_duration"):
            value = data.get(name)
            if value is not None and not (isinstance(value, str) and (value == "" or DURATION_RE.match(value))):
                raise ValueError(f"{name} must look like 14d, 12h or 1mo, got {value!r}")

        return cls(request_id=rid, stage=stage, team=str(data.get("team") or ""), alias=data.get("alias"),
                   models=tuple(models), budget_usd=num("budget_usd", float), rpm=num("rpm", int), tpm=num("tpm", int),
                   duration=data.get("duration") or None, budget_duration=data.get("budget_duration") or None,
                   requester_email=data.get("requester_email"), ref=data.get("ref"), extra=dict(data))


@dataclass(frozen=True)
class Provisioned:
    key_alias: str
    secret_ref: str
    expires_at: str | None
    fetch_hint: str
    models: tuple[str, ...]
    reused: bool = False


def candidate_aliases(prefix: str, request_id: str, stage: str) -> list[str]:
    hexid = request_id.replace("-", "").lower()
    return [f"{prefix}-{hexid[:8]}-{stage}", f"{prefix}-{hexid}-{stage}"]


def _expires(value) -> str | None:
    if not value:
        return None
    try:
        return iso(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return str(value)


class Provisioner:
    def __init__(self, settings: Settings, admin: LiteLLMAdmin, sink: SecretSink, connection_factory=None):
        self.settings = settings
        self.admin = admin
        self.sink = sink
        self.connection = connection_factory or db.connection

    # -- pieces -------------------------------------------------------------------------------------------
    def gateway_models(self, models: tuple[str, ...]) -> tuple[str, ...]:
        """Catalog ids -> gateway aliases (MODEL_ALIAS_MAP, identity by default); DEFAULT_MODELS if none."""
        out: list[str] = []
        for model in models or self.settings.default_models:
            for alias in self.settings.model_alias_map.get(model, (model,)):
                if alias not in out:
                    out.append(alias)
        return tuple(out)

    def secret_path(self, key_alias: str, req: Approved) -> str:
        try:
            path = self.settings.path_template.format(key_alias=key_alias, request_id=req.request_id,
                                                      request_id8=req.request_id.replace("-", "")[:8], stage=req.stage)
        except (KeyError, IndexError, ValueError) as exc:
            raise ProvisionError(f"SECRET_PATH_TEMPLATE cannot be rendered: {exc}") from None
        try:
            return check_path(path)
        except SinkError as exc:
            raise ProvisionError(str(exc)) from None

    def key_body(self, key_alias: str, req: Approved, models: tuple[str, ...]) -> dict:
        body = {"key_alias": key_alias, "models": list(models), "duration": req.duration,
                "metadata": {"request_id": req.request_id, "team": req.team, "requester": req.alias,
                             "stage": req.stage, "managed_by": SOURCE}}
        if req.budget_usd is not None:
            body["max_budget"] = req.budget_usd
        if req.budget_duration:
            body["budget_duration"] = req.budget_duration
        if req.rpm:
            body["rpm_limit"] = req.rpm
        if req.tpm:
            body["tpm_limit"] = req.tpm
        return body

    # -- the work -----------------------------------------------------------------------------------------
    def provision(self, req: Approved) -> Provisioned:
        models = self.gateway_models(req.models)
        if not models:
            # LiteLLM treats an empty model list as "every model": never create such a key.
            raise ProvisionError("no models to grant: the request lists none and DEFAULT_MODELS is empty")
        for key_alias in candidate_aliases(self.settings.alias_prefix, req.request_id, req.stage):
            existing = self.admin.find_key(key_alias)
            owner = str(((existing or {}).get("metadata") or {}).get("request_id") or "")
            if existing and owner != req.request_id:
                log.warning("key alias belongs to another request; trying the long alias",
                            extra=kv(key_alias=key_alias, request_id=req.request_id))
                continue
            path = self.secret_path(key_alias, req)
            if existing:
                stored = self.sink.describe(path)
                if stored and stored.token_id and stored.token_id == existing.get("token"):
                    log.info("key already provisioned", extra=kv(request_id=req.request_id, key_alias=key_alias))
                    return Provisioned(key_alias, stored.ref, _expires(existing.get("expires")),
                                       self.sink.fetch_hint(stored.ref, path), models, reused=True)
                log.warning("replacing a key that was never stored", extra=kv(request_id=req.request_id, key_alias=key_alias))
                self.admin.delete_key(existing["token"])
            return self._create(key_alias, path, req, models)
        raise ProvisionError("every key alias candidate belongs to another request")

    def _create(self, key_alias: str, path: str, req: Approved, models: tuple[str, ...]) -> Provisioned:
        created = self.admin.generate(self.key_body(key_alias, req, models))
        key = created.pop("key")
        token_id = created.get("token_id") or created.get("token") or hashlib.sha256(key.encode()).hexdigest()
        expires = _expires(created.get("expires"))
        secret = {"api_key": key, "key_alias": key_alias, "models": list(models), "stage": req.stage,
                  "expires_at": expires, "request_id": req.request_id}
        if self.settings.gateway_public_url:
            secret["base_url"] = self.settings.gateway_public_url
        meta = {"key_alias": key_alias, "token_id": token_id, "request_id": req.request_id, "stage": req.stage}
        try:
            ref = self.sink.put(path, secret, meta)
        except Exception:
            try:  # a key nobody can fetch is useless: remove it now (a retry would replace it anyway)
                self.admin.delete_key(token_id)
            except AdminError:
                pass
            raise
        finally:
            del key, secret
        log.info("key provisioned", extra=kv(request_id=req.request_id, key_alias=key_alias, stage=req.stage,
                                             sink=self.sink.name, models=list(models), expires_at=expires))
        return Provisioned(key_alias, ref, expires, self.sink.fetch_hint(ref, path), models)

    # -- events -------------------------------------------------------------------------------------------
    def _common(self, req: Approved | dict) -> dict:
        get = (lambda k: getattr(req, k)) if isinstance(req, Approved) else req.get
        return {"ref": get("ref"), "team": get("team"), "alias": get("alias"), "requester_email": get("requester_email")}

    def publish_provisioned(self, req: Approved, result: Provisioned, natural_id) -> None:
        payload = {"request_id": req.request_id, "key_alias": result.key_alias, "secret_ref": result.secret_ref,
                   "stage": req.stage, "expires_at": result.expires_at, **self._common(req),
                   "models": list(result.models), "fetch_hint": result.fetch_hint, "reused": result.reused}
        with self.connection() as conn:
            queue.publish(conn, "key.provisioned", payload, source=SOURCE,
                          dedupe_key=f"key.provisioned:{req.request_id}:{req.stage}:{natural_id}")

    def publish_failed(self, data: Approved | dict, error: str, natural_id) -> None:
        rid = data.request_id if isinstance(data, Approved) else str(data.get("request_id"))
        stage = data.stage if isinstance(data, Approved) else data.get("stage")
        key_alias = candidate_aliases(self.settings.alias_prefix, rid, stage)[0] if stage in STAGES else None
        payload = {"request_id": rid, "error": error, "stage": stage, "key_alias": key_alias, **self._common(data)}
        with self.connection() as conn:
            queue.publish(conn, "key.failed", payload, source=SOURCE, dedupe_key=f"key.failed:{rid}:{stage}:{natural_id}")
        log.error("key provisioning failed", extra=kv(request_id=rid, stage=stage, error=error))

    # -- worker handler -----------------------------------------------------------------------------------
    def handle(self, job: Job) -> None:
        if job.event != "request.approved":
            log.info("event ignored", extra=kv(job_id=job.id, event=job.event))
            return
        data = job.data
        natural_id = job.payload.get("event_id") or f"job{job.id}"
        if not data.get("request_id"):
            raise PermanentError("request.approved without request_id")
        try:
            req = Approved.parse(data)
        except ValueError as exc:
            self.publish_failed(data, f"invalid request.approved payload: {exc}", natural_id)
            raise PermanentError(str(exc)) from None
        try:
            result = self.provision(req)
        except (RetryLater, PermanentError):
            raise
        except Exception as exc:  # noqa: BLE001 - classify, then retry or report
            transient = getattr(exc, "transient", True)
            message = redact(str(exc)) or type(exc).__name__
            if transient and job.attempts < job.max_attempts:
                delay = min(900.0, self.settings.retry_base_seconds * 2 ** max(0, job.attempts - 1))
                raise RetryLater(message, delay=delay) from None
            self.publish_failed(req, message, natural_id)
            raise PermanentError(message) from None
        self.publish_provisioned(req, result, natural_id)

    def ready(self) -> None:
        db.ping()
        self.admin.ping()
