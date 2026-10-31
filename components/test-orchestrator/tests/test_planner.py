"""Planning against PostgreSQL: routes x items, datasets, not_run rows, derived SEC-11, SEC-16 once per gateway,
baselines, batching and errors."""
from __future__ import annotations

import pytest
from conftest import approve

from test_orchestrator.planner import PlanError, batch, routes_for_item
from test_orchestrator.routes import list_routes


def jobs(db):
    with db.connection() as c:
        return [r["payload"] for r in c.execute("SELECT payload FROM platform.jobs WHERE queue = 'test-runner' ORDER BY id").fetchall()]


def test_plan_for_a_model(db, store, orch):
    approve(store, "SEC-01", "SEC-04", "SEC-07", "SEC-10")
    with db.connection() as conn:
        plan = orch.plan(conn, trigger="manual", model_id="cand")
    assert plan["purpose"] == "pre-release" and plan["status"] == "running"
    assert sorted(plan["routes"]) == ["direct:bedrock:us-east-1:cand", "gw:onprem:cand"]  # disabled anthropic route left out
    payloads = jobs(db)
    assert len(payloads) == plan["jobs_total"] > 0
    sent = {(p["route"]["id"], i["sec_id"]) for p in payloads for i in p["items"]}
    assert sent == {
        ("gw:onprem:cand", "SEC-01"), ("gw:onprem:cand", "SEC-04"), ("gw:onprem:cand", "SEC-07"), ("gw:onprem:cand", "SEC-10"),
        ("gw:onprem:cand", "SEC-16"),
        ("direct:bedrock:us-east-1:cand", "SEC-01"), ("direct:bedrock:us-east-1:cand", "SEC-04"),
        ("direct:bedrock:us-east-1:cand", "SEC-10"),
    }
    p0 = payloads[0]
    assert p0["job_key"].startswith(plan["run_id"] + "|") and p0["judge"] == {"model": "judge"}
    assert p0["settings"] == {"max_tokens": 2048, "seed": 7, "gateway_version": None}
    sec01 = next(i for p in payloads for i in p["items"] if i["sec_id"] == "SEC-01")
    assert sec01 == {"sec_id": "SEC-01", "dataset": {"dataset_id": "ds-sec-01", "version": 1}, "n": 300, "trials": 3,
                     "rule": {"type": "not_worse_than_baseline", "fallback": {"type": "max_rate", "max": 0.1}}}
    sec16 = next(i for p in payloads for i in p["items"] if i["sec_id"] == "SEC-16")
    assert "dataset" not in sec16 and sec16["n"] == 0
    with db.connection() as c:
        not_run = {(r["route_id"], r["sec_id"]) for r in c.execute("SELECT route_id, sec_id FROM tests.results WHERE verdict = 'not_run'")}
        row = c.execute("SELECT * FROM tests.plans").fetchone()
        dedupe = {r["dedupe_key"] for r in c.execute("SELECT dedupe_key FROM platform.jobs WHERE queue = 'test-runner'")}
    assert ("gw:onprem:cand", "SEC-02") in not_run and ("direct:bedrock:us-east-1:cand", "SEC-02") not in not_run
    assert ("direct:bedrock:us-east-1:cand", "SEC-09") in not_run and not any(s == "SEC-17" for _, s in not_run)
    assert row["jobs_total"] == len(payloads) and row["trigger_detail"]["purpose"] == "pre-release" and row["model_id"] == "cand"
    assert dedupe == {p["job_key"] for p in payloads}
    run = store.get_json("llmops-runs", f"{plan['run_id']}/run.json")
    modes = {i["sec_id"]: i["mode"] for i in run["items"]}
    assert modes["SEC-11"] == "derived" and modes["SEC-16"] == "check" and modes["SEC-02"] == "not_run" and modes["SEC-01"] == "dataset"
    assert "SEC-17" not in modes and run["judge_model"] == "judge"


def test_baseline_routes_join_the_plan(db, store, orch):
    orch.settings.baseline_model_id = "prod"
    approve(store, "SEC-01", "SEC-04")
    with db.connection() as conn:
        plan = orch.plan(conn, trigger="manual", model_id="cand")
    payloads = jobs(db)
    by_route = {}
    for p in payloads:
        by_route.setdefault(p["route"]["id"], []).extend(p["items"])
    cand = next(i for i in by_route["gw:onprem:cand"] if i["sec_id"] == "SEC-01")
    assert cand["baseline"] == {"route_id": "gw:onprem:prod", "model_id": "prod"}
    direct = next(i for i in by_route["direct:bedrock:us-east-1:cand"] if i["sec_id"] == "SEC-01")
    assert direct["baseline"]["route_id"] == "direct:bedrock:us-east-1:prod"
    assert [i["sec_id"] for i in by_route["gw:onprem:prod"]] == ["SEC-01"]  # only the relative item runs on the baseline
    assert "baseline" not in by_route["gw:onprem:prod"][0]
    assert "gw:onprem:prod" in plan["routes"]


def test_baseline_model_in_the_same_plan_runs_each_item_once(db, store, orch):
    orch.settings.baseline_model_id = "prod"
    approve(store, "SEC-01")
    with db.connection() as conn:
        orch.plan(conn, trigger="manual", route_ids=["gw:onprem:cand", "gw:onprem:prod"], sec_ids=["SEC-01"])
    sent = [(p["route"]["id"], i["sec_id"], "baseline" in i) for p in jobs(db) for i in p["items"]]
    assert sorted(sent) == [("gw:onprem:cand", "SEC-01", True), ("gw:onprem:prod", "SEC-01", False)]


def test_subset_skips_not_runnable_and_is_adhoc(db, store, orch):
    approve(store, "SEC-04")
    with db.connection() as conn:
        plan = orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-04", "SEC-17"])
    assert plan["skipped"] == ["SEC-17"] and plan["purpose"] == "adhoc"
    assert {i["sec_id"] for p in jobs(db) for i in p["items"]} == {"SEC-04"}


def test_plan_errors(db, store, orch):
    with db.connection() as conn:
        with pytest.raises(PlanError, match="unknown SEC") as e:
            orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-99"])
        assert e.value.status == 400
        with pytest.raises(PlanError, match="unknown or disabled") as e:
            orch.plan(conn, trigger="manual", route_ids=["gw:onprem:nope"])
        assert e.value.status == 404
        with pytest.raises(PlanError, match="no enabled routes"):
            orch.plan(conn, trigger="manual", model_id="ghost")
        with pytest.raises(PlanError, match="purpose"):
            orch.plan(conn, trigger="manual", model_id="cand", purpose="fun")


def test_plan_without_any_job_completes_at_once(db, store, orch):
    with db.connection() as conn:
        plan = orch.plan(conn, trigger="manual", model_id="cand", sec_ids=["SEC-03"])  # no dataset approved
    assert plan["jobs_total"] == 0 and plan["status"] == "failed"
    with db.connection() as c:
        ev = c.execute("SELECT payload FROM platform.events WHERE type = 'test.run.completed'").fetchone()["payload"]
        verdict = c.execute("SELECT verdict FROM tests.results WHERE sec_id = 'SEC-03'").fetchone()["verdict"]
    assert ev == {"plan_id": plan["plan_id"], "run_id": plan["run_id"], "status": "failed"} and verdict == "not_run"


def test_routes_for_item_and_batching(db, orch):
    with db.connection() as conn:
        routes = list_routes(conn)
    gw = {"routes": "gateway", "layer": "app", "n": 10}
    assert {r["kind"] for r in routes_for_item(gw, routes)} == {"gateway"}
    assert len(routes_for_item({"routes": "primary", "n": 10}, routes)) == 1
    assert routes_for_item({"routes": "none"}, routes) == []
    assert [r["id"] for r in routes_for_item({"routes": "gateway", "layer": "platform", "n": 0}, routes)] == ["gw:onprem:cand"]
    items = [{"sec_id": "a", "n": 300, "trials": 3}, {"sec_id": "b", "n": 200, "trials": 3}, {"sec_id": "c", "n": 50, "trials": 1},
             {"sec_id": "d", "n": 1000, "trials": 3}]
    assert [[i["sec_id"] for i in b] for b in batch(items, 1500)] == [["a", "b"], ["c"], ["d"]]
