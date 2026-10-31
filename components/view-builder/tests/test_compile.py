import datetime as dt
import json

import pytest

from conftest import EDITORIAL, MATRIX, SECURITY_RUN, without_additions
from view_builder.compile import BuildError, Inputs, availability, build_view, matrix_view
from view_builder.security import from_legacy_run, to_portal_security
from view_builder.store_io import encode_view, local_inputs


def _real_inputs(portal_data):
    return local_inputs(portal_data, security_run=SECURITY_RUN, matrix=MATRIX, editorial=EDITORIAL)


def test_reproduces_fixture_exactly(portal_data, fixture_view):
    """Same build date as the prototype (2026-10-30): apart from the fields added since (availability, gateway info,
    ZDR), the compiled view equals the contract fixture byte for byte."""
    inp = _real_inputs(portal_data)
    view = build_view(inp, dt.date(2026, 10, 30))
    assert inp.warnings == []
    assert view["repo"] == "yunistsao-maker/llmops-portal@8f56627"
    assert encode_view(without_additions(view)) == encode_view(fixture_view)
    assert list(view) == list(fixture_view) + ["gw"]


def test_availability_and_gateway_from_real_data(portal_data):
    view = build_view(_real_inputs(portal_data), dt.date(2026, 10, 30))
    av = {m["id"]: m["avail"] for m in view["models"]}
    # G6 blocked by the 2026-10-12 security run
    assert av["claude-sonnet-5-5"] == {"state": "paused", "eta": None, "next_gate": "G6", "basis": "gates"}
    # G0 and G1 done: G2 10 + max(G3..G7) 5 + G8 14 + G9 2 = 31 days
    assert av["claude-opus-5-5"] == {"state": "evaluating", "eta": "2026-11-30", "next_gate": "G2", "basis": "gates"}
    # G1 still in progress (no deep dive): 3 more days
    assert av["gemini-4-argon"]["eta"] == "2026-12-03" and av["gemini-4-argon"]["next_gate"] == "G1"
    opus = next(m for m in view["models"] if m["id"] == "claude-opus-5-5")
    assert opus["gw"]["alias"] == "claude-opus-5-5" and opus["gw"]["providers"][1]["model"] == "bedrock/global.anthropic.claude-opus-5-5"
    assert opus["offs"]["aws"]["zdr"] == "unknown" and opus["offs"]["azure"]["zdr"] == "available"
    assert view["gw"]["recommended"] == "v1.105.2" and view["gw"]["minimum"] == "v1.103.6" and "1.107.0" in view["gw"]["avoid"]


def test_availability_rules():
    d = dt.date(2026, 11, 1)
    done = {g: "done" for g in ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")}
    assert availability({**done, "G9": "done"}, d) == {"state": "available", "eta": None, "next_gate": None, "basis": "gates"}
    assert availability({**done, "G8": "doing"}, d) == {"state": "trial", "eta": "2026-11-17", "next_gate": "G8", "basis": "gates"}
    assert availability({"G0": "done", "G3": "blocked"}, d)["state"] == "paused"
    # parallel gates count once, as the longest still open (G5 5 days); G6 done does not shorten it
    ev = availability({"G0": "done", "G1": "done", "G2": "done", "G6": "done"}, d)
    assert ev == {"state": "evaluating", "eta": "2026-11-22", "next_gate": "G3", "basis": "gates"}
    # durations from the editorial overlay
    cfg = {"durations": {"G8": 7, "G5": 1, "bogus": 3, "G9": True}}
    assert availability({"G0": "done", "G1": "done", "G2": "done"}, d, cfg)["eta"] == "2026-11-15"  # G8 7 + G9 2 + G6 5
    # overrides: state, eta, note; bad values are reported, not applied
    warn = []
    o = availability({"G0": "done"}, d, None, {"state": "available", "note": "已開通"}, warn, "m")
    assert o == {"state": "available", "eta": None, "next_gate": None, "basis": "editorial", "note": "已開通"}
    o = availability({"G0": "done"}, d, None, {"eta": "2027-01-15"}, warn, "m")
    assert o["state"] == "evaluating" and o["eta"] == "2027-01-15" and o["basis"] == "editorial"
    o = availability({"G0": "done"}, d, None, {"state": "soon", "eta": "next week"}, warn, "m")
    assert o["basis"] == "gates" and len(warn) == 2


def test_today_keeps_keys_and_shapes(portal_data, fixture_view):
    """Built on a later day only the date-relative parts move: `built`, the 30-day news window and ret.days
    (and the availability ETAs, which count from the build date)."""
    full = build_view(_real_inputs(portal_data), dt.date(2026, 10, 31))
    assert next(m for m in full["models"] if m["id"] == "claude-opus-5-5")["avail"]["eta"] == "2026-12-01"
    view = without_additions(full)
    assert list(view) == list(fixture_view)
    for key in ("models", "open", "actions", "hl", "cdNew", "cdPrice", "gates", "brief"):
        assert view[key] == fixture_view[key], key
    assert view["security"] == fixture_view["security"] and view["secMatrix"] == fixture_view["secMatrix"]
    assert view["built"] == "2026-10-31"
    # news: cards last seen on 2026-10-01 leave the window (2026-10-02 .. 2026-10-31)
    dropped = [n for n in fixture_view["news"] if n["last"] < "2026-10-02"]
    assert len(view["news"]) == len(fixture_view["news"]) - len(dropped) == 107
    # retirements: one day later, every `days` is one smaller and the event 14 days back drops out
    assert len(view["ret"]) == 85 and fixture_view["ret"][0]["days"] == -14
    assert all(r["days"] == f["days"] - 1 for r, f in zip(view["ret"], fixture_view["ret"][1:]))
    # records keep their shape
    for key in ("news", "ret"):
        assert list(view[key][0]) == list(fixture_view[key][0])


def test_matrix_view_matches_fixture(fixture_view):
    mx = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix_view(mx) == fixture_view["secMatrix"]
    assert matrix_view(None)["items"] == []


def test_legacy_security_conversion(portal_data, fixture_view):
    raw = json.loads(SECURITY_RUN.read_text(encoding="utf-8"))
    sec = from_legacy_run(raw)
    assert sec["runId"] == "20261012T151346-a55616" and sec["judge"] == "Opus 5.5"
    assert set(sec["models"]) == set(fixture_view["security"]["models"])
    assert to_portal_security(sec) is sec
    with pytest.raises(ValueError):
        to_portal_security({"hello": 1})


def test_minimal_inputs_without_overlays(mini_data):
    inp = local_inputs(mini_data, source="test")
    view = build_view(inp, dt.date(2026, 10, 30))
    assert view["repo"] == "test" and view["asOf"] == "2026-10-24"
    alpha, beta = view["models"]
    # no editorial copy: catalog text, English falls back to Chinese, no translations of breaking changes
    assert alpha["oneZ"] == "Alpha 是測試模型。" and alpha["oneE"] == "" and alpha["hiE"] == alpha["hiZ"] == ["很快"]
    assert alpha["brE"] == [] and alpha["breaking"] == [{"t": "改了", "sev": "high"}] and alpha["deepDive"]
    assert alpha["offs"]["aws"]["taipei"] == "global" and alpha["offs"]["aws"]["price"] == {"scope": "global", "in": 1.0, "out": 5.0}
    assert beta["offs"] == {} and beta["aa"] is None
    assert any("no editorial copy" in w for w in inp.warnings)
    # old radar card outside the window, retirement 59 days ago outside the 14-day tail
    assert [n["id"] for n in view["news"]] == ["it-aaaaaaaaaa"] and view["news"][0]["models"] == ["alpha-1"]
    assert [r["id"] for r in view["ret"]] == ["e1"] and view["ret"][0]["days"] == 16
    assert view["open"][0]["host"] == {"aws": "ga"}
    assert view["security"]["models"] == {} and view["security"]["items"] == []
    assert view["secMatrix"]["items"] == [] and view["hl"] == [] and view["mock"] is None and view["gw"] is None
    assert alpha["avail"]["state"] == "evaluating" and alpha["gw"] == {"alias": "alpha-1", "providers": [], "minVersion": None,
                                                                         "recVersion": None, "caveats": []}
    assert view["progress"]["alpha-1"] == {"G0": "done", "G1": "done"} and view["progress"]["beta-2"]["G1"] == "doing"


def test_editorial_patches_and_highlights(mini_data):
    editorial = {
        "highlights": [{"id": "it-bbbbbbbbbb", "zh": "舊的重點", "en": "Old highlight"}, {"id": "it-missing", "zh": "x"}],
        "countdown": {"new": [{"date": None, "zh": "未定", "en": "TBD", "link": "#news"}],
                      "price": [{"date": "not-a-date", "zh": "壞日期"}]},
        "models": {"alpha-1": {"oneZ": "白話", "oneE": "Plain", "hiZ": ["a"], "hiE": ["A"], "cauZ": ["c"], "cauE": ["C"],
                               "brE": ["Changed", "extra line"]}},
        "offerings": [{"model": "alpha-1", "cloud": "aws", "set": {"so": "card-updated", "soNote": "n"}},
                      {"model": "alpha-1", "cloud": "gcp", "add": {"status": "private-preview", "since": "2026-10-21",
                                                                   "note": "預覽", "service": "agent-platform"}},
                      {"model": "alpha-1", "cloud": "aws", "default": {"geoPrice": {"scope": "geo", "in": 1.1, "out": 5.5}}}],
    }
    inp = Inputs(data=local_inputs(mini_data, source="t").data, editorial=editorial, source="t")
    view = build_view(inp, dt.date(2026, 10, 30))
    alpha = view["models"][0]
    assert alpha["oneE"] == "Plain" and alpha["hiE"] == ["A"]
    assert alpha["brE"] == []  # 2 lines for 1 breaking change: dropped with a warning
    assert alpha["offs"]["aws"]["so"] == "card-updated" and alpha["offs"]["aws"]["geoPrice"]["in"] == 1.1
    assert alpha["offs"]["gcp"]["status"] == "private-preview" and alpha["offs"]["gcp"]["service"] == "agent-platform"
    # highlight older than the window is filled from the radar card; unknown ids are skipped
    assert [h["id"] for h in view["hl"]] == ["it-bbbbbbbbbb"] and view["hl"][0]["last"] == "2026-08-02"
    assert view["cdNew"][0]["we"] == "" and view["cdPrice"] == []
    assert len([w for w in inp.warnings if "highlight" in w or "countdown" in w or "brE" in w]) == 3


def test_security_names_come_from_the_matrix(mini_data):
    sec = {"runId": "r1", "date": "2026-10-12", "purpose": "x", "litellm": "1", "judge": "j", "cases": 1,
           "models": {"alpha-1": {"label": "Alpha", "counts": {"pass": 0, "fail": 1, "review": 0, "info": 0},
                                  "issues": [{"id": "SEC-01", "t": "old", "v": "fail", "rate": 0.5, "rule": "r"}]}},
           "items": [{"id": "SEC-01", "t": "old", "tier": "A", "rule": "r", "res": {"Alpha": {"v": "fail", "rate": 0.5, "n": 2}}},
                     {"id": "SEC-99", "t": "unknown item", "tier": "B", "rule": "r", "res": {}}]}
    inp = Inputs(data=local_inputs(mini_data, source="t").data, security=sec,
                 matrix=json.loads(MATRIX.read_text(encoding="utf-8")), source="t")
    view = build_view(inp, dt.date(2026, 10, 30))
    items = view["security"]["items"]
    assert items[0]["t"] == "直接 prompt injection / jailbreak" and items[0]["tEn"] == "Direct prompt injection / jailbreak"
    assert items[1]["tEn"] == "unknown item"
    assert view["security"]["models"]["alpha-1"]["issues"][0]["tEn"].startswith("Direct")
    assert view["progress"]["alpha-1"]["G6"] == "blocked"
    assert sec["items"][0]["t"] == "old"  # input not mutated


def test_bad_inputs_raise_build_error(mini_data):
    data = local_inputs(mini_data, source="t").data
    with pytest.raises(BuildError):
        build_view(Inputs(data={k: v for k, v in data.items() if k != "radar.json"}), dt.date(2026, 10, 30))
    broken = dict(data, **{"catalog.json": {"models": [{"id": "../evil"}]}})
    with pytest.raises(BuildError):
        build_view(Inputs(data=broken), dt.date(2026, 10, 30))
    broken = dict(data, **{"lifecycle.json": {"events": "nope"}})
    with pytest.raises(BuildError):
        build_view(Inputs(data=broken), dt.date(2026, 10, 30))
