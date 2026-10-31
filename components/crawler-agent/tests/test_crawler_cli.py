"""``python -m crawler_agent``: the daily run as a job (plan, settings checked before anything runs, roles that fail,
SIGTERM, and research.proposal.ready)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from crawler_agent import cli, events
from crawler_agent.daily import RoleOutcome
from helpers_platform import RELEASE, publish_snapshot
from research.offline.scripted_model import Ctx, ScriptedModel, call, say

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def _clean_env(**extra) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": f"{ROOT / 'src'}:{REPO / 'libs/llmops-common/src'}",
           "HOME": os.environ.get("HOME", "/tmp")}
    env.update(extra)
    return env


def test_help_works_without_any_settings():
    for argv in (["--help"], ["daily", "--help"], ["role", "--help"], ["roles"]):
        proc = subprocess.run([sys.executable, "-m", "crawler_agent", *argv], env=_clean_env(), capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
    assert "radar-editor" in proc.stdout and "bedrock-researcher" in proc.stdout
    bad = subprocess.run([sys.executable, "-m", "crawler_agent", "role", "nobody"], env=_clean_env(), capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2


@pytest.fixture()
def platform(memory_store, monkeypatch):
    """A curated bucket holding a live release; settings as on the platform (scripted model, no events by default)."""
    publish_snapshot(memory_store)
    monkeypatch.setenv("MODEL_PROVIDER", "scripted")
    monkeypatch.delenv("BUCKET_CURATED", raising=False)
    return memory_store


def _logs(capsys) -> list[dict]:
    lines = []
    for line in capsys.readouterr().out.splitlines():
        try:
            lines.append(json.loads(line))
        except ValueError:
            pass
    return lines


def _smoke(step):
    """The offline smoke script, with a summary of the role's own (two roles never write the very same proposal)."""
    return ScriptedModel([
        [say("Checking the portal."), call("lookup_portal", file="summary")],
        [call("submit_proposal", proposal={"summary": f"離線測試（{step.role}）：沒有提出任何變更。", "ops": [],
                                            "checked": [{"topic": "portal snapshot loaded", "result": "ok"}]})],
    ])


def test_configuration_is_checked_before_anything_runs(platform, monkeypatch, capsys):
    monkeypatch.setenv("MODEL_PROVIDER", "gateway")
    assert cli.main(["daily", "--no-event"]) == cli.EXIT_CONFIG  # no LLM_GATEWAY_URL
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://llm-gateway:4000")
    monkeypatch.setenv("SEARCH_BACKEND", "acme")
    assert cli.main(["daily", "--no-event"]) == cli.EXIT_CONFIG  # an unknown search provider
    monkeypatch.delenv("SEARCH_BACKEND")
    assert cli.main(["daily"]) == cli.EXIT_CONFIG  # events need DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/llmops")
    monkeypatch.setenv("OUTPUT_URI", "s3://some-other-bucket/research/")
    assert cli.main(["daily"]) == cli.EXIT_CONFIG  # the publisher could not read the proposals
    logs = _logs(capsys)
    errors = [entry["error"] for entry in logs if entry.get("msg") == "not started: configuration problem"]
    assert len(errors) == 4 and "LLM_GATEWAY_URL" in errors[0] and "SEARCH_BACKEND" in errors[1] and "DATABASE_URL" in errors[2]
    assert "curated bucket" in errors[3]
    assert all(entry["component"] == "crawler-agent" for entry in logs)
    assert not any(o.key.startswith("research/") for o in platform.list("llmops-curated"))  # nothing ran


def test_a_platform_without_a_release_is_told_how_to_bootstrap(memory_store, monkeypatch, capsys):
    monkeypatch.setenv("MODEL_PROVIDER", "scripted")
    assert cli.main(["role", "litellm-researcher", "--no-event"]) == cli.EXIT_CONFIG
    error = next(e["error"] for e in _logs(capsys) if e.get("msg") == "not started: configuration problem")
    assert "python -m publisher bootstrap" in error


def test_the_daily_run_saves_every_role_s_results_in_the_curated_bucket(platform, capsys):
    code = cli.main(["daily", "--roles", "radar-editor,litellm-researcher", "--no-event"], model_factory=_smoke)
    assert code == cli.EXIT_OK
    keys = sorted(o.key for o in platform.list("llmops-curated", "research/"))
    assert len([k for k in keys if k.startswith("research/runs/")]) == 2
    assert len([k for k in keys if k.startswith("research/proposals/")]) == 2
    notes = [k for k in keys if k.startswith("research/handoff/2026-10-02/")]
    assert len(notes) == 2 and sum("-litellm-researcher-" in k for k in notes) == 1
    logs = _logs(capsys)
    started = [e["role"] for e in logs if e.get("msg") == "role started"]
    assert started == ["litellm-researcher", "radar-editor"]  # always in the daily order
    finished = next(e for e in logs if e.get("msg") == "research run finished")
    assert finished["exit_code"] == 0 and finished["counts"] == {"accepted": 2}
    assert all(k.startswith("research/proposals/") for k in finished["proposal_keys"]) and len(finished["proposal_keys"]) == 2
    proposal = json.loads(platform.get_bytes("llmops-curated", finished["proposal_keys"][0]))
    assert proposal["portal"]["manifest"]["releaseId"] == RELEASE and proposal["task"]["role"] == "litellm-researcher"
    assert any(e.get("msg") == "tool call" and e.get("tool") == "lookup_portal" for e in logs)


def test_a_role_that_fails_does_not_stop_the_others(platform, capsys):
    def factory(step):
        if step.role == "gcp-researcher":
            def boom(ctx: Ctx):
                raise RuntimeError("gateway unreachable")
            return ScriptedModel([boom])
        return _smoke(step)

    code = cli.main(["daily", "--roles", "azure-researcher,gcp-researcher,vendor-api-researcher", "--no-event"], model_factory=factory)
    assert code == cli.EXIT_INCOMPLETE
    finished = next(e for e in _logs(capsys) if e.get("msg") == "research run finished")
    assert finished["failed"] == ["gcp-researcher"] and finished["counts"] == {"accepted": 2, "error": 1}


def test_sigterm_stops_taking_roles_cancels_the_running_one_and_keeps_what_is_done(platform, capsys):
    def factory(step):
        if step.role == "gcp-researcher":
            def terminate(ctx: Ctx):
                os.kill(os.getpid(), signal.SIGTERM)  # the kubelet stopping the pod
                return [say("still reading"), call("lookup_portal", file="summary")]
            return ScriptedModel([terminate, [call("lookup_portal", file="summary")]] * 20)
        return _smoke(step)

    code = cli.main(["daily", "--roles", "azure-researcher,gcp-researcher,vendor-api-researcher", "--no-event"], model_factory=factory)
    assert code == cli.EXIT_TERMINATED
    logs = _logs(capsys)
    finished = next(e for e in logs if e.get("msg") == "research run finished")
    assert finished["counts"] == {"accepted": 1, "cancelled": 1, "skipped": 1}
    assert len(finished["proposal_keys"]) == 1  # azure's proposal is kept and announced
    assert [e["role"] for e in logs if e.get("msg") == "role started"] == ["azure-researcher", "gcp-researcher"]


def test_a_dry_run_writes_nothing(platform):
    assert cli.main(["role", "litellm-researcher", "--dry-run"], model_factory=_smoke) == cli.EXIT_OK
    assert not any(o.key.startswith("research/") for o in platform.list("llmops-curated"))


def test_the_event_names_the_curated_keys_of_accepted_proposals_only():
    outcomes = [RoleOutcome("bedrock-researcher", "accepted", proposal_id="prop-a", saved="s3://llmops-curated/research/proposals/prop-a.json"),
                RoleOutcome("azure-researcher", "no_proposal", saved="s3://llmops-curated/research/runs/run-b.json"),
                RoleOutcome("gcp-researcher", "error"),
                RoleOutcome("radar-editor", "accepted", proposal_id="prop-c", saved="s3://llmops-curated/research/proposals/prop-c.json")]
    body = events.payload(run_date="2026-10-02", run_id="20261002T031700-a1b2c3", outcomes=outcomes, curated_bucket="llmops-curated",
                          roles=["bedrock-researcher", "azure-researcher", "gcp-researcher", "radar-editor"])
    assert body == {"run_date": "2026-10-02", "run_id": "20261002T031700-a1b2c3",
                    "proposal_keys": ["research/proposals/prop-a.json", "research/proposals/prop-c.json"],
                    "roles": ["bedrock-researcher", "azure-researcher", "gcp-researcher", "radar-editor"]}
    assert events.check_payload(body) is body
    twice = events.payload(run_date="2026-10-02", run_id="r", outcomes=[outcomes[0], outcomes[0]], curated_bucket="llmops-curated", roles=[])
    assert twice["proposal_keys"] == ["research/proposals/prop-a.json"]
    assert events.proposal_key("s3://elsewhere/research/proposals/x.json", "llmops-curated") is None
    for bad in ({**body, "extra": 1}, {**body, "proposal_keys": ["portal/manifest.json"]}, {**body, "run_date": "today"}, []):
        with pytest.raises(ValueError):
            events.check_payload(bad)


# ---------------------------------------------------------------------------------------------- with PostgreSQL
def test_the_daily_run_publishes_research_proposal_ready_once(platform, db, capsys):
    code = cli.main(["daily", "--roles", "litellm-researcher,radar-editor"], model_factory=_smoke)
    assert code == cli.EXIT_OK
    with db.connection() as conn:
        evs = conn.execute("SELECT id, type, payload, source, dedupe_key FROM platform.events").fetchall()
        jobs = conn.execute("SELECT queue, payload, dedupe_key FROM platform.jobs").fetchall()
    assert len(evs) == 1 and evs[0]["type"] == "research.proposal.ready" and evs[0]["source"] == "crawler-agent"
    body = evs[0]["payload"]
    assert set(body) == {"run_date", "run_id", "proposal_keys", "roles"} and body["run_date"] == "2026-10-02"
    assert body["roles"] == ["litellm-researcher", "radar-editor"] and len(body["proposal_keys"]) == 2
    assert evs[0]["dedupe_key"] == f"research.proposal.ready:{body['run_id']}"
    for key in body["proposal_keys"]:
        assert platform.exists("llmops-curated", key)
    assert [(j["queue"], j["payload"]["event"], j["payload"]["payload"]) for j in jobs] == [("publisher", "research.proposal.ready", body)]

    # a logged payload can be announced again; the dedupe key keeps it to one event
    capsys.readouterr()
    assert cli.main(["announce", "--payload", json.dumps(body)]) == cli.EXIT_OK
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM platform.events").fetchone()["n"] == 1
    assert cli.main(["announce", "--payload", '{"run_id": "x"}']) == cli.EXIT_USAGE


def test_a_run_whose_event_cannot_be_published_says_what_to_send_again(platform, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrong@127.0.0.1:9/none")
    monkeypatch.setattr(events.time, "sleep", lambda s: None)
    from llmops_common import db as dbmod

    dbmod.close_pool()
    monkeypatch.setattr(dbmod, "_get_pool", lambda: (_ for _ in ()).throw(ConnectionError("database down")))
    assert cli.main(["role", "litellm-researcher"], model_factory=_smoke) == cli.EXIT_INCOMPLETE
    failed = next(e for e in _logs(capsys) if e.get("msg", "").startswith("could not publish research.proposal.ready"))
    assert failed["payload"]["roles"] == ["litellm-researcher"] and len(failed["payload"]["proposal_keys"]) == 1
