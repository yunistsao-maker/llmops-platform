"""assistant-api routes.

POST /api/chat         {question, history?, lang?}  -> {answer, links: [{route, title}], truncated}
POST /api/assist/form  {messages, form, lang?}      -> {reply, questions, form}
Errors: {"error": {"code", "message"}, "links"?: [...]} with 401/413/422/429/502/503.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from llmops_common.auth import Authenticator, Principal
from llmops_common.config import ConfigError, env, env_float, env_int
from llmops_common.fastapi_support import install_access_log, install_health, user_dependency
from llmops_common.llm import GatewayClient, GatewayError
from llmops_common.log import get_logger, kv

from .content import ContentIndex
from .errors import error_response, install_error_handlers
from .limits import BodyLimit, Concurrency, RateLimiter
from .prompts import DATA_CLASSES, MODEL_ID, chat_rules, form_rules, sanitize_form
from .prompts import _number as number
from .retrieval import Index, filter_links

log = get_logger("assistant_api")

REFUSAL_CODES = {"content_filter", "content_policy_violation", "contentpolicyviolationerror", "refusal", "safety"}


class Turn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str
    history: list[Turn] = Field(default_factory=list, max_length=40)
    lang: Literal["zh", "en"] = "zh"


class FormRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    messages: list[Turn] = Field(min_length=1, max_length=40)
    form: dict = Field(default_factory=dict)
    lang: Literal["zh", "en"] = "zh"


class Settings:
    def __init__(self):
        self.max_question = env_int("MAX_QUESTION_CHARS", 1000)
        self.max_turn = env_int("MAX_TURN_CHARS", 4000)
        self.history_turns = env_int("HISTORY_TURNS", 6)
        self.chat_max_tokens = env_int("CHAT_MAX_TOKENS", 1024)
        self.form_max_tokens = env_int("FORM_MAX_TOKENS", 1500)
        self.form_model = env("LLM_FORM_MODEL", None)
        tz = env("PORTAL_TZ", "Asia/Taipei")
        try:
            self.tz = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"PORTAL_TZ={tz!r} is not a known time zone") from exc


def gateway_from_env() -> GatewayClient:
    return GatewayClient(env("LLM_GATEWAY_URL"), env("LLM_GATEWAY_KEY", None), env("LLM_MODEL", "portal-quick"),
                         timeout=env_float("LLM_TIMEOUT", 30.0), max_retries=env_int("LLM_MAX_RETRIES", 1))


def clean_history(turns: list[Turn], keep: int, max_chars: int) -> list[dict]:
    """Last `keep` turns, starting with a user turn, alternating roles (the prototype sent chat.turns.slice(-6))."""
    out: list[dict] = []
    for t in turns[-keep:] if keep > 0 else []:
        msg = {"role": t.role, "content": t.content[:max_chars]}
        if out and out[-1]["role"] == msg["role"]:
            out[-1] = msg  # keep the later of two same-role turns
        else:
            out.append(msg)
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def clean_form_input(f: dict) -> dict:
    """The form the user has filled so far (request-schema names), reduced to safe, bounded values for the prompt."""
    f = f if isinstance(f, dict) else {}

    def text(key, limit):
        v = f.get(key)
        return v.strip()[:limit] if isinstance(v, str) else ""

    models = [m for m in f.get("models") or [] if isinstance(m, str) and MODEL_ID.match(m)][:10]
    date = f.get("target_date") if isinstance(f.get("target_date"), str) else None
    try:
        date = dt.date.fromisoformat(date).isoformat() if date else None
    except ValueError:
        date = None
    return {"team": text("team", 120), "use_case": text("use_case", 1000), "models": models,
            "data_class": f.get("data_class") if f.get("data_class") in DATA_CLASSES else "",
            "data_class_other": text("data_class_other", 200),
            "cloud": f.get("cloud") if f.get("cloud") in ("any", "aws", "azure", "gcp") else "any",
            "rpd": number(f.get("rpd")), "tokens_in": number(f.get("tokens_in")), "tokens_out": number(f.get("tokens_out")),
            "target_date": date, "external": f.get("external") is True, "taiwan_only": f.get("taiwan_only") is True,
            "note": text("note", 1000)}


def gateway_failure(exc: GatewayError, links: list[dict]) -> JSONResponse:
    status, code = exc.status, str(exc.code or "").lower()
    if code == "invalid_json":
        return error_response(502, "invalid_reply", "the model did not return a usable answer", links=links)
    if status == 429:
        return error_response(429, "rate_limited", "the model gateway is busy; try again shortly",
                              headers={"Retry-After": "20"}, links=links)
    if code in REFUSAL_CODES or (status == 400 and ("content" in code or "safety" in code)):
        return error_response(422, "refused", "the model declined to answer this question", links=links)
    if status is None or status in (408, 503, 504):
        return error_response(503, "gateway_unavailable", "the model gateway is not reachable right now",
                              headers={"Retry-After": "30"}, links=links)
    if status in (401, 403):
        log.error("gateway rejected our key", extra=kv(status=status))
        return error_response(502, "gateway_auth", "the assistant is misconfigured (gateway key)", links=links)
    return error_response(502, "gateway_error", f"the model gateway failed ({status})", links=links)


def create_app(*, store=None, bucket: str | None = None, authenticator: Authenticator | None = None,
               gateway: GatewayClient | None = None, content: ContentIndex | None = None, today=None,
               start_polling: bool = True) -> FastAPI:
    if content is None:
        if store is None:
            from llmops_common.storage import S3Store

            store = S3Store.from_env()
        if bucket is None:
            from llmops_common.storage import bucket as bucket_name

            bucket = bucket_name("curated")
        content = ContentIndex(store, bucket, poll_seconds=env_float("INDEX_POLL_SECONDS", 30.0))
    authenticator = authenticator or Authenticator.from_env()
    gateway = gateway or gateway_from_env()
    settings = Settings()
    limiter = RateLimiter(env_int("RATE_LIMIT_PER_MINUTE", 10), 60.0)
    slots = Concurrency(env_int("MAX_CONCURRENT_CALLS", 8), env_float("QUEUE_WAIT_SECONDS", 5.0))

    def local_today() -> dt.date:
        return today() if today else dt.datetime.now(settings.tz).date()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_polling:
            content.start()
        else:
            content.safe_refresh()
        yield
        content.stop()
        gateway.close()

    app = FastAPI(title="assistant-api", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    install_error_handlers(app)
    install_access_log(app)
    install_health(app, content.ping)
    app.add_middleware(BodyLimit, max_bytes=env_int("MAX_BODY_BYTES", 65536))
    app.state.content = content
    current_user = user_dependency(authenticator)

    def admit(user: Principal) -> tuple[Index | None, JSONResponse | None]:
        wait = limiter.check(user.sub)
        if wait:
            return None, error_response(429, "rate_limited", "too many questions; wait a moment",
                                        headers={"Retry-After": str(int(wait + 0.999))})
        index = content.ensure()
        if index is None:
            return None, error_response(503, "no_content", "the portal content is not available yet",
                                        headers={"Retry-After": "30"})
        return index, None

    def call(fn, links):
        if not slots.acquire():
            return None, error_response(503, "busy", "the assistant is busy; try again shortly",
                                        headers={"Retry-After": "10"}, links=links)
        try:
            return fn(), None
        except GatewayError as exc:
            log.warning("gateway call failed", extra=kv(status=exc.status, code=exc.code))
            return None, gateway_failure(exc, links)
        finally:
            slots.release()

    @app.post("/api/chat")
    def chat(req: ChatRequest, user: Principal = Depends(current_user)):
        question = req.question.strip()
        if not question:
            return error_response(422, "invalid_request", "question is empty")
        if len(question) > settings.max_question:
            return error_response(422, "question_too_long", f"questions are limited to {settings.max_question} characters")
        index, refused = admit(user)
        if refused:
            return refused
        started = time.monotonic()
        hits = index.search(question, 8)
        links = index.links(hits, req.lang)
        messages = ([{"role": "system", "content": chat_rules(index.view, local_today(), req.lang)}]
                    + clean_history(req.history, settings.history_turns, settings.max_turn)
                    + [{"role": "user", "content": f"{question}\n\n{index.context(hits)}"}])
        result, failed = call(lambda: gateway.chat(messages, max_tokens=settings.chat_max_tokens), links)
        if failed:
            return failed
        log.info("chat answered", extra=kv(hits=len(hits), view_id=index.view_id, model=result.model,
                                           finish=result.finish_reason, ms=int((time.monotonic() - started) * 1000),
                                           tokens=(result.usage or {}).get("total_tokens")))
        if result.finish_reason == "content_filter":
            return error_response(422, "refused", "the model declined to answer this question", links=links)
        return {"answer": filter_links(result.text, index).strip(), "links": links,
                "truncated": result.finish_reason == "length"}

    @app.post("/api/assist/form")
    def assist_form(req: FormRequest, user: Principal = Depends(current_user)):
        turns = clean_history(req.messages, settings.history_turns + 1, settings.max_turn)
        if not turns or turns[-1]["role"] != "user" or not turns[-1]["content"].strip():
            return error_response(422, "invalid_request", "the last message must be the user's question")
        if len(req.messages[-1].content.strip()) > settings.max_question:
            return error_response(422, "question_too_long", f"questions are limited to {settings.max_question} characters")
        index, refused = admit(user)
        if refused:
            return refused
        form = clean_form_input(req.form)
        messages = [{"role": "system", "content": form_rules(index.view, form, local_today(), req.lang)}] + turns
        res, failed = call(lambda: gateway.chat_json(messages, model=settings.form_model,
                                                     max_tokens=settings.form_max_tokens), [])
        if failed:
            return failed
        res = res if isinstance(res, dict) else {}
        reply = res.get("reply").strip() if isinstance(res.get("reply"), str) else ""
        questions = [q.strip() for q in res.get("questions") or [] if isinstance(q, str) and q.strip()][:2]
        suggested = sanitize_form(res.get("form"), set(index.by_id))
        if not reply and not suggested:
            return error_response(502, "invalid_reply", "the model did not return a usable suggestion")
        log.info("form suggestion", extra=kv(view_id=index.view_id, fields=sorted(suggested)))
        return {"reply": filter_links(reply, index), "questions": questions, "form": suggested}

    return app
