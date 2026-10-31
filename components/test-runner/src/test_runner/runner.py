"""One runner job (contracts/test-jobs.md): cases from the datasets bucket -> target route -> score -> runs bucket,
tests.results and test.job.completed.

Per item: sample n cases (seeded), run every case x trial on the route (RUNNER_CONCURRENCY in parallel), score with
the ported secsuite scorers (judge through the gateway), summarise, then write
    runs/{run_id}/{route_id}/{sec_id}.jsonl         case-level lines (response text redacted and clipped)
    runs/{run_id}/{route_id}/{sec_id}.summary.json  the summary (written after the lines: it marks the item complete)
and upsert tests.results. A retried job skips items whose summary exists, so a worker that dies mid-job loses only the
item it was on. test.job.completed is published once per job (dedupe key test.job.completed:<job_key>): status done,
or failed after the last attempt / a permanent error (its unfinished items are recorded with verdict error).
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from llmops_common import db as dbmod
from llmops_common.config import env, env_float, env_int
from llmops_common.ids import iso
from llmops_common.log import get_logger, kv, redact
from llmops_common.queue import Job, PermanentError, RetryLater, publish
from llmops_common.storage import bucket

from . import __version__, scoring, stats, supply_chain
from .cases import CaseSource, sample_item

log = get_logger("test_runner")
SOURCE = "test-runner"
GATEWAY_CHECK_ITEMS = {"SEC-16"}


@dataclass
class RunnerConfig:
    concurrency: int = 4
    store_text: str = "flagged"            # all | flagged (fail / review / error) | none
    text_max_chars: int = 4000
    error_share: float = stats.ERROR_SHARE
    default_max_tokens: int = 2048
    default_seed: int = 7
    heartbeat_seconds: float = 60.0
    osv_url: str = "https://api.osv.dev/v1/query"
    pypi_url: str = "https://pypi.org/pypi/litellm/json"

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        store_text = env("STORE_RESPONSE_TEXT", "flagged")
        if store_text not in ("all", "flagged", "none"):
            raise ValueError("STORE_RESPONSE_TEXT must be all, flagged or none")
        return cls(concurrency=max(1, env_int("RUNNER_CONCURRENCY", 4)), store_text=store_text,
                   text_max_chars=env_int("RESPONSE_TEXT_MAX_CHARS", 4000), error_share=env_float("ERROR_SHARE", stats.ERROR_SHARE),
                   default_max_tokens=env_int("DEFAULT_MAX_TOKENS", 2048), default_seed=env_int("SAMPLE_SEED", 7),
                   heartbeat_seconds=env_float("JOB_HEARTBEAT_SECONDS", 60.0),
                   osv_url=env("OSV_API_URL", cls.osv_url), pypi_url=env("PYPI_LITELLM_URL", cls.pypi_url))


# ---------------------------------------------------------------------------
# one case (port of secsuite run.execute)
# ---------------------------------------------------------------------------
def _clip(s, n=4000):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + f" …[已截斷，共 {len(s)} 字元]"


def _show_response(r, n=4000):
    parts = []
    if r.get("text"):
        parts.append(r["text"])
    for t in r.get("tool_calls") or []:
        parts.append(f"[tool call] {t['name']}({json.dumps(t['arguments'], ensure_ascii=False)})")
    if r.get("refusal"):
        parts.append(f"[stop_reason=refusal，category={r.get('refusal_category')}]")
    if r.get("error"):
        parts.append(f"[error] {r['error']}")
    return _clip("\n".join(parts) or f"[空白回應，stop_reason={r.get('stop_reason')}]", n)


def execute(provider, case: dict, max_tokens: int, trial: int, judge) -> dict:
    """Run one case against the route and score it. Never raises: failures become outcome "error"."""
    scorer = case["scorer"]
    max_tokens = max(int(max_tokens), int(case.get("max_tokens") or 0))
    hint = {"id": case["id"], "scorer": scorer, "expect": case.get("expect"), "trial": trial}
    system = case.get("system")
    responses: list[dict] = []
    if case["kind"] == "multi":
        history: list[dict] = []
        for i, turn in enumerate(case["turns"]):
            history.append({"role": "user", "content": turn})
            r = provider.call(system, list(history), None, max_tokens, {**hint, "turn": i})
            responses.append(r)
            if r.get("error") or r.get("refusal") or not r.get("text"):
                break
            history.append({"role": "assistant", "content": r["text"]})
        shown = _show_response(responses[-1])
    elif case["kind"] == "pair":
        a = provider.call(system, case["messages"], None, max_tokens, {**hint, "variant": "a"})
        b = provider.call(system, case["messages_b"], None, max_tokens, {**hint, "variant": "b"})
        responses = [a, b]
        shown = "[基準] " + _show_response(a, 1500) + "\n\n[變體] " + _show_response(b, 2000)
    else:
        r = provider.call(system, case["messages"], case.get("tools"), max_tokens, hint)
        responses = [r]
        shown = _show_response(r)
    judge_models: list[str] = []

    def recording_judge(prompt):
        out = judge(prompt)
        if out.get("model"):
            judge_models.append(out["model"])
        return out

    verdict = scoring.score(scorer, case, responses, recording_judge if judge else None)
    last = responses[-1]
    return {
        "case_id": case["id"], "source": case["dataset"], "category": case["category"], "lang": case.get("lang"),
        "trial": trial, "outcome": verdict["outcome"], "label": verdict["label"], "detail": verdict.get("detail", ""),
        "value": verdict.get("value"), "model": last.get("model"), "stop_reason": last.get("stop_reason"),
        "refusal": any(r.get("refusal") for r in responses), "refusal_category": last.get("refusal_category"),
        "rerouted": any(r.get("rerouted") for r in responses), "calls": len(responses),
        "latency_ms": sum(r.get("latency_ms", 0) for r in responses),
        "input_tokens": sum(r["usage"]["input"] for r in responses), "output_tokens": sum(r["usage"]["output"] for r in responses),
        "judge_models": judge_models, "pair": (case.get("expect") or {}).get("pair"), "response": shown,
    }


def error_record(case: dict, trial: int, exc: BaseException) -> dict:
    return {"case_id": case["id"], "source": case["dataset"], "category": case["category"], "lang": case.get("lang"),
            "trial": trial, "outcome": "error", "label": "runner_error", "detail": redact(f"{type(exc).__name__}: {exc}")[:300],
            "value": None, "model": None, "stop_reason": None, "refusal": False, "rerouted": False, "calls": 0,
            "pair": None, "response": ""}


# ---------------------------------------------------------------------------
# job
# ---------------------------------------------------------------------------
class Heartbeat(threading.Thread):
    """Keeps the job's lease fresh while a long job runs, so another replica does not take it over."""

    def __init__(self, connection, job_id: int, worker_id: str, interval: float):
        super().__init__(name=f"heartbeat-{job_id}", daemon=True)
        self.connection, self.job_id, self.worker_id, self.interval = connection, job_id, worker_id, interval
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.interval):
            try:
                with self.connection() as conn:
                    conn.execute("UPDATE platform.jobs SET locked_at = now() WHERE id = %s AND locked_by = %s AND status = 'running'",
                                 (self.job_id, self.worker_id))
            except Exception as exc:  # noqa: BLE001 - the lease simply expires later
                log.warning("heartbeat failed", extra=kv(job_id=self.job_id, error=type(exc).__name__))

    def stop(self):
        self._stop.set()


def validate(payload: dict) -> dict:
    missing = [k for k in ("plan_id", "run_id", "job_key", "route", "items") if not payload.get(k)]
    if missing:
        raise PermanentError(f"job payload lacks {', '.join(missing)}")
    route = payload["route"]
    for k in ("id", "model_id", "provider"):
        if not route.get(k):
            raise PermanentError(f"job route lacks {k}")
    for it in payload["items"]:
        if not it.get("sec_id"):
            raise PermanentError("job item lacks sec_id")
        if it["sec_id"] not in GATEWAY_CHECK_ITEMS and not (it.get("dataset") or {}).get("dataset_id"):
            raise PermanentError(f"{it['sec_id']}: job item lacks dataset")
    return payload


class JobRunner:
    def __init__(self, *, store, providers, judges, config: RunnerConfig | None = None, connection=None,
                 worker_id: str = "runner", cancel: threading.Event | None = None, http=None):
        self.store = store
        self.providers = providers
        self.judges = judges
        self.config = config or RunnerConfig()
        self.connection = connection or dbmod.connection
        self.worker_id = worker_id
        self.cancel = cancel or threading.Event()
        self._http = http
        self.cases = CaseSource(store, bucket("datasets"))
        self.runs_bucket = bucket("runs")

    def __call__(self, job: Job) -> None:
        self.handle(job)

    # -- keys -------------------------------------------------------------------------------------
    @staticmethod
    def cases_key(run_id: str, route_id: str, sec_id: str) -> str:
        return f"{run_id}/{route_id}/{sec_id}.jsonl"

    @staticmethod
    def summary_key(run_id: str, route_id: str, sec_id: str) -> str:
        return f"{run_id}/{route_id}/{sec_id}.summary.json"

    # -- entry ------------------------------------------------------------------------------------
    def handle(self, job: Job) -> None:
        payload = job.data
        try:
            spec = validate(payload)
        except PermanentError as exc:
            if all(payload.get(k) for k in ("plan_id", "run_id", "job_key")):
                self._publish(payload, "failed", str(exc))
            raise
        heartbeat = None
        if self.config.heartbeat_seconds > 0:
            heartbeat = Heartbeat(self.connection, job.id, self.worker_id, self.config.heartbeat_seconds)
            heartbeat.start()
        try:
            self.run(spec)
        except RetryLater:
            raise
        except PermanentError as exc:
            self.fail(spec, str(exc))
            raise
        except Exception as exc:
            if job.attempts >= job.max_attempts:
                self.fail(spec, f"{type(exc).__name__}: {exc}")
                raise PermanentError(f"last attempt failed: {type(exc).__name__}: {exc}") from exc
            raise
        finally:
            if heartbeat:
                heartbeat.stop()

    def run(self, spec: dict) -> list[dict]:
        route = spec["route"]
        started = time.monotonic()
        provider = self.providers.make(route)
        judge = self.judges.make((spec.get("judge") or {}).get("model"))
        summaries = []
        for item in spec["items"]:
            if self.cancel.is_set():
                raise RetryLater("worker is shutting down", delay=0)
            key = self.summary_key(spec["run_id"], route["id"], item["sec_id"])
            existing = self.store.get_json(self.runs_bucket, key, default=None)
            if existing:  # finished by an earlier attempt of this job
                self.upsert(existing)
                summaries.append(existing)
                continue
            summary = self.run_item(spec, item, provider, judge)
            summaries.append(summary)
        self._publish(spec, "done", None)
        log.info("job done", extra=kv(run_id=spec["run_id"], route_id=route["id"], items=[s["sec_id"] for s in summaries],
                                      verdicts={s["sec_id"]: s["verdict"] for s in summaries},
                                      seconds=int(time.monotonic() - started)))
        return summaries

    # -- one item -----------------------------------------------------------------------------------
    def run_item(self, spec: dict, item: dict, provider, judge) -> dict:
        route, sec_id = spec["route"], item["sec_id"]
        settings = spec.get("settings") or {}
        started_at = iso()
        trials = max(1, int(item.get("trials") or 1))
        dataset_label = None
        if sec_id in GATEWAY_CHECK_ITEMS:
            records = self.gateway_checks(settings)
            n = len(records)
            trials = 1
        else:
            ds = item["dataset"]
            dataset_label = f"{ds['dataset_id']}@v{int(ds['version'])}"
            all_cases = self.cases.load(sec_id, ds["dataset_id"], ds["version"])
            seed = int(settings.get("seed", self.config.default_seed))
            chosen = sample_item(all_cases, int(item.get("n") or len(all_cases)), seed)
            n = len(chosen)
            max_tokens = int(settings.get("max_tokens") or self.config.default_max_tokens)
            records = self.execute_cases(provider, judge, chosen, trials, max_tokens)
        s = stats.summarise(records)
        verdict, note, rule = stats.verdict(item.get("rule") or {"type": "info"}, s,
                                            baseline_planned=bool(item.get("baseline")), error_share=self.config.error_share)
        if item.get("baseline"):
            rule["baseline_route"] = item["baseline"].get("route_id")
        cases_key = self.cases_key(spec["run_id"], route["id"], sec_id)
        lines = [self._line(spec, sec_id, r) for r in records]
        self.store.put_jsonl(self.runs_bucket, cases_key, lines)
        summary = {
            "run_id": spec["run_id"], "plan_id": spec["plan_id"], "job_key": spec["job_key"], "route_id": route["id"],
            "model_id": route["model_id"], "sec_id": sec_id, "dataset": dataset_label, "n": n, "n_planned": item.get("n"),
            "trials": trials, "failures": s["failures"], "errors": s["errors"], "rate": s["rate"], "upper95": s["upper95"],
            "verdict": verdict, "rule": rule, "models_seen": s["models_seen"], "stop_reasons": s["stop_reasons"],
            "note": note, **{k: v for k, v in s.items() if k not in ("failures", "errors", "rate", "upper95", "models_seen", "stop_reasons")},
            "seed": settings.get("seed", self.config.default_seed) if sec_id not in GATEWAY_CHECK_ITEMS else None,
            "cases_key": cases_key, "started_at": started_at, "finished_at": iso(), "runner": __version__,
        }
        if sec_id in GATEWAY_CHECK_ITEMS:
            summary["gateway_version"] = settings.get("gateway_version")
        self.store.put_json(self.runs_bucket, self.summary_key(spec["run_id"], route["id"], sec_id), summary)
        self.upsert(summary)
        log.info("item done", extra=kv(run_id=spec["run_id"], route_id=route["id"], sec_id=sec_id, n=n, trials=trials,
                                       failures=s["failures"], errors=s["errors"], reviews=s["reviews"], verdict=verdict))
        return summary

    def execute_cases(self, provider, judge, cases: list[dict], trials: int, max_tokens: int) -> list[dict]:
        tasks = [(i, case, t) for i, case in enumerate(cases) for t in range(trials)]
        results: dict[tuple[int, int], dict] = {}
        pool = ThreadPoolExecutor(max_workers=self.config.concurrency, thread_name_prefix="case")

        def work(case, trial):
            try:
                return execute(provider, case, max_tokens, trial, judge)
            except Exception as exc:  # noqa: BLE001 - a scorer bug must not lose the other cases
                return error_record(case, trial, exc)

        futures = {pool.submit(work, case, t): (i, t) for i, case, t in tasks}
        pending = set(futures)
        try:
            while pending:
                if self.cancel.is_set():
                    raise RetryLater("worker is shutting down", delay=0)
                done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for f in done:
                    results[futures[f]] = f.result()
        finally:
            pool.shutdown(wait=not self.cancel.is_set(), cancel_futures=True)
        return [results[k] for k in sorted(results)]

    def gateway_checks(self, settings: dict) -> list[dict]:
        version = settings.get("gateway_version")
        if not version and env("TARGET_GATEWAY_URL", None):
            try:
                version = self.providers.gateway_client().gateway_version()
            except Exception:  # noqa: BLE001
                version = None
        import httpx

        http = self._http or httpx.Client(timeout=40)
        try:
            policy = supply_chain.load_policy(self.store, bucket("curated"))
            return supply_chain.check(version, http, policy, osv_url=self.config.osv_url, pypi_url=self.config.pypi_url)
        finally:
            if self._http is None:
                http.close()

    def _line(self, spec: dict, sec_id: str, r: dict) -> dict:
        keep = self.config.store_text == "all" or (self.config.store_text == "flagged" and r["outcome"] in ("fail", "review", "error"))
        line = {"run_id": spec["run_id"], "route_id": spec["route"]["id"], "sec_id": sec_id,
                **{k: v for k, v in r.items() if k not in ("response", "judge_models")}}
        line["detail"] = redact(_clip(line.get("detail"), 1000))
        if r.get("judge_models"):
            line["judge_model"] = r["judge_models"][-1]
        if keep and r.get("response"):
            line["response"] = redact(_clip(r["response"], self.config.text_max_chars))
        return line

    # -- database / events ------------------------------------------------------------------------------
    def upsert(self, summary: dict) -> None:
        from psycopg.types.json import Jsonb

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO tests.results (run_id, route_id, sec_id, dataset, n, trials, failures, errors, rate, upper95,
                                           verdict, rule, models_seen, stop_reasons, details_key)
                VALUES (%(run_id)s, %(route_id)s, %(sec_id)s, %(dataset)s, %(n)s, %(trials)s, %(failures)s, %(errors)s,
                        %(rate)s, %(upper95)s, %(verdict)s, %(rule)s, %(models_seen)s, %(stop_reasons)s, %(details_key)s)
                ON CONFLICT (run_id, route_id, sec_id) DO UPDATE SET
                    dataset = EXCLUDED.dataset, n = EXCLUDED.n, trials = EXCLUDED.trials, failures = EXCLUDED.failures,
                    errors = EXCLUDED.errors, rate = EXCLUDED.rate, upper95 = EXCLUDED.upper95, verdict = EXCLUDED.verdict,
                    rule = EXCLUDED.rule, models_seen = EXCLUDED.models_seen, stop_reasons = EXCLUDED.stop_reasons,
                    details_key = EXCLUDED.details_key
                """,
                {"run_id": summary["run_id"], "route_id": summary["route_id"], "sec_id": summary["sec_id"],
                 "dataset": summary.get("dataset"), "n": int(summary.get("n") or 0), "trials": int(summary.get("trials") or 1),
                 "failures": int(summary.get("failures") or 0), "errors": int(summary.get("errors") or 0),
                 "rate": summary.get("rate"), "upper95": summary.get("upper95"), "verdict": summary["verdict"],
                 "rule": Jsonb(summary.get("rule") or {}), "models_seen": Jsonb(summary.get("models_seen") or {}),
                 "stop_reasons": Jsonb(summary.get("stop_reasons") or {}), "details_key": summary.get("cases_key")},
            )

    def fail(self, spec: dict, error: str) -> None:
        """Last attempt failed: record the unfinished items as error, then tell the orchestrator."""
        error = (redact(error) or "")[:500]
        route = spec["route"]
        for item in spec["items"]:
            key = self.summary_key(spec["run_id"], route["id"], item["sec_id"])
            try:
                if self.store.get_json(self.runs_bucket, key, default=None):
                    continue
                ds = item.get("dataset") or {}
                summary = {"run_id": spec["run_id"], "plan_id": spec["plan_id"], "job_key": spec["job_key"], "route_id": route["id"],
                           "model_id": route["model_id"], "sec_id": item["sec_id"],
                           "dataset": f"{ds['dataset_id']}@v{int(ds['version'])}" if ds.get("dataset_id") else None,
                           "n": 0, "trials": int(item.get("trials") or 1), "failures": 0, "errors": 0, "rate": None, "upper95": None,
                           "verdict": "error", "rule": dict(item.get("rule") or {}), "models_seen": {}, "stop_reasons": {},
                           "note": f"job failed: {error}", "cases_key": None, "finished_at": iso(), "runner": __version__}
                self.store.put_json(self.runs_bucket, key, summary)
                self.upsert(summary)
            except Exception:  # noqa: BLE001 - still publish the failure below
                log.exception("could not record failed item", extra=kv(run_id=spec["run_id"], sec_id=item["sec_id"]))
        self._publish(spec, "failed", error)
        log.error("job failed", extra=kv(run_id=spec["run_id"], route_id=route["id"], job_key=spec["job_key"], error=error))

    def _publish(self, spec: dict, status: str, error: str | None) -> None:
        payload = {"plan_id": spec["plan_id"], "run_id": spec["run_id"], "job_key": spec["job_key"],
                   "route_id": (spec.get("route") or {}).get("id"),
                   "sec_ids": [it.get("sec_id") for it in spec.get("items") or []], "status": status, "error": error}
        with self.connection() as conn:
            publish(conn, "test.job.completed", payload, source=SOURCE, dedupe_key=f"test.job.completed:{spec['job_key']}")
