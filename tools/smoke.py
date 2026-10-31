#!/usr/bin/env python3
"""End-to-end smoke test through portal-web, for the local stack (make up) or a test cluster behind a tunnel.

    python3 tools/smoke.py [--base http://localhost:8080] [--skip tests,chat,...]

Checks, in order (each prints ok / FAIL and the run exits non-zero if any failed):
  web        portal-web answers /healthz and serves the page
  content    /api/content/portal.json has models (needs a release: seed or publisher)
  chat       /api/chat answers (through assistant-api and the gateway)
  request    a Security C request goes straight to provisioning and ends active with a key alias;
             a Security B request waits for G2, gets approved, and ends active
  subscribe  subscribe / list / unsubscribe for a model
  tests      plans SEC-01 on the mock route, waits for the run, reads its results
Dev auth only (AUTH_MODE=dev): no token is sent. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

FAILED: list[str] = []


def call(base: str, method: str, path: str, body=None, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, (json.loads(raw) if raw and "json" in ctype else raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)
    return ok


def wait_for(fn, timeout: float, every: float = 2.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last[0]:
            return last
        time.sleep(every)
    return last


def t_web(base):
    st, _ = call(base, "GET", "/healthz")
    check("web /healthz", st == 200, str(st))
    st, page = call(base, "GET", "/")
    check("web page", st == 200 and isinstance(page, str) and "<html" in page.lower(), str(st))


def t_content(base):
    st, view = call(base, "GET", "/api/content/portal.json")
    n = len(view.get("models", [])) if isinstance(view, dict) else 0
    check("content portal.json", st == 200 and n > 0, f"{st}, {n} models")


def t_chat(base):
    def asked():  # assistant-api picks up a new view within INDEX_POLL_SECONDS
        s, a = call(base, "POST", "/api/chat", {"question": "Opus 5.5 什麼時候可以申請？", "lang": "zh"}, timeout=90)
        return (s != 503, (s, a))
    _, (st, ans) = wait_for(asked, 60, 5)
    text = ans.get("answer") or ans.get("text") or "" if isinstance(ans, dict) else ""
    check("chat answer", st == 200 and bool(text), f"{st} {str(ans)[:120] if st != 200 else ''}")


def t_request(base):
    form = {"team": "Smoke Test", "use_case": "smoke test of the request flow", "data_class": "sec-c",
            "models": ["claude-sonnet-5-5"], "rpd": 100}
    st, r = call(base, "POST", "/api/requests", form)
    if not check("request submit (Security C)", st == 201, f"{st} {r if st != 201 else ''}"):
        return
    rid = r["id"]

    def active():
        s, cur = call(base, "GET", f"/api/requests/{rid}")
        return (s == 200 and cur.get("status") in ("active", "trial", "failed"), cur)
    _, cur = wait_for(active, 90)
    key = (cur or {}).get("key") or {}
    check("request provisioned", (cur or {}).get("status") == "active" and bool(key.get("alias") or key.get("key_alias")),
          f"status {(cur or {}).get('status')}")

    st, r = call(base, "POST", "/api/requests", {**form, "data_class": "sec-b", "team": "Smoke Test B"})
    if not check("request submit (Security B)", st == 201 and r.get("next_gate") == "G2", f"{st} next {r.get('next_gate') if isinstance(r, dict) else r}"):
        return
    rid = r["id"]
    st, d = call(base, "POST", f"/api/requests/{rid}/decisions", {"gate": "G2", "decision": "approve", "comment": "smoke"})
    check("request G2 approve", st in (200, 201), f"{st} {d if st not in (200, 201) else ''}")
    _, cur = wait_for(active, 90)
    check("request B provisioned", (cur or {}).get("status") == "active", f"status {(cur or {}).get('status')}")


def t_subscribe(base):
    st, _ = call(base, "PUT", "/api/subscriptions/claude-opus-5-5")
    check("subscribe", st in (200, 201), str(st))
    st, lst = call(base, "GET", "/api/subscriptions")
    ids = [i.get("model_id") for i in lst.get("items", [])] if isinstance(lst, dict) else []
    check("subscriptions list", st == 200 and "claude-opus-5-5" in ids, str(st))
    st, _ = call(base, "DELETE", "/api/subscriptions/claude-opus-5-5")
    check("unsubscribe", st == 204, str(st))


def t_tests(base):
    _, (st, routes) = wait_for(lambda: (lambda r: (r[0] == 200, r))(call(base, "GET", "/api/tests/routes")), 60, 3)
    items = (routes.get("routes") or routes.get("items") or []) if isinstance(routes, dict) else routes
    ids = [r.get("id") for r in (items or []) if isinstance(r, dict)]
    mock = next((i for i in ids if i and i.startswith("direct:mock:")), None)
    gw = next((i for i in ids if i and i.startswith("gw:")), None)
    if not check("tests routes", st == 200 and bool(mock) and bool(gw), f"{st}, {len(ids)} routes"):
        return
    # one direct route (mock provider) and one route through the gateway; the judge always goes through the gateway
    st, plan = call(base, "POST", "/api/tests/plans",
                    {"route_ids": [mock, gw], "sec_ids": ["SEC-01"], "trigger": "manual", "purpose": "adhoc"})
    if not check("tests plan", st == 201 and plan.get("jobs_total", 0) > 0, f"{st} {plan if st != 201 else ''}"):
        return
    pid, run_id = plan["plan_id"], plan["run_id"]

    def done():
        s, p = call(base, "GET", f"/api/tests/plans/{pid}")
        return (s == 200 and p.get("status") in ("done", "failed", "completed"), p)
    _, p = wait_for(done, 180, 3)
    check("tests run finished", (p or {}).get("status") in ("done", "completed"), f"status {(p or {}).get('status')}")
    st, run = call(base, "GET", f"/api/tests/runs/{run_id}")
    rows = run.get("results", []) if isinstance(run, dict) else []
    verdicts = {r.get("route_id"): r.get("verdict") for r in rows}
    check("tests results", st == 200 and bool(verdicts) and all(v in ("pass", "fail", "review") for v in verdicts.values()),
          f"{verdicts}")


STEPS = {"web": t_web, "content": t_content, "chat": t_chat, "request": t_request, "subscribe": t_subscribe,
         "tests": t_tests}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--skip", default="", help="comma-separated steps to skip: " + ",".join(STEPS))
    a = ap.parse_args()
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    for name, fn in STEPS.items():
        if name in skip:
            print(f"skip {name}")
            continue
        try:
            fn(a.base.rstrip("/"))
        except Exception as exc:  # report and continue with the next step
            check(name, False, f"{type(exc).__name__}: {exc}")
    print(f"\n{'all passed' if not FAILED else 'failed: ' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
