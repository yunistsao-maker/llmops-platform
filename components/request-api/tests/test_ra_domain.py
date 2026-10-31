import datetime as dt
import uuid

import pytest
from ra_support import ROOT, valid_request

from request_api import domain, store
from request_api.settings import ConfigError, LimitSettings, Settings
from request_api.validation import RequestValidator, ValidationFailed, document_from_row

LS = LimitSettings()
SCHEMA = ROOT / "contracts/schemas/request.schema.json"


def test_limits_formula_examples():
    trial = domain.compute_limits({"rpd": 5000, "tokens_in": 3000, "tokens_out": 500, "models": []}, "trial", LS)
    # rpm = ceil(5000 x 10 / 1440) = 35; tpm = 35 x 3500; per day = 5000 x (3000x3 + 500x15) / 1e6 = 82.5 USD
    assert (trial["rpm"], trial["tpm"]) == (35, 122_500)
    assert trial["budget_usd"] == 1733.0                 # ceil(82.5 x 14 x 1.5)
    assert trial["duration"] == "14d" and trial["budget_duration"] is None
    full = domain.compute_limits({"rpd": 5000, "tokens_in": 3000, "tokens_out": 500}, "full", LS)
    assert full["budget_usd"] == 3713.0 and full["duration"] is None and full["budget_duration"] == "30d"


def test_limits_defaults_clamps_and_prices():
    unknown = domain.compute_limits({"rpd": 0, "tokens_in": 0, "tokens_out": 0}, "full", LS)
    assert unknown["basis"]["rpd"] == 1000 and unknown["rpm"] == 10          # ceil(6.9) -> floor of 10
    assert unknown["tpm"] == 25_000 and unknown["budget_usd"] == 608.0       # 1000 x (2000x3+500x15)/1e6 x 30 x 1.5
    huge = domain.compute_limits({"rpd": 100_000_000, "tokens_in": 10_000_000, "tokens_out": 1}, "full", LS)
    assert huge["rpm"] == 6000 and huge["tpm"] == 10_000_000 and huge["budget_usd"] == 20_000.0
    tiny = domain.compute_limits({"rpd": 10, "tokens_in": 10, "tokens_out": 10}, "trial", LS)
    assert tiny["budget_usd"] == 20.0 and tiny["tpm"] == 10_000
    priced = LimitSettings(model_prices={"claude-opus-5-5": (15.0, 75.0)})
    assert domain.prices_for(["claude-haiku-5-5", "claude-opus-5-5"], priced) == (15.0, 75.0)
    assert domain.prices_for(["claude-haiku-5-5"], priced) == (3.0, 15.0)


def test_limit_settings_from_env(monkeypatch):
    monkeypatch.setenv("BUDGET_MODEL_PRICES", '{"claude-opus-5-5": {"in": 15, "out": 75}, "x": [1, 2]}')
    monkeypatch.setenv("FULL_KEY_DURATION", "365d")
    monkeypatch.setenv("LIMIT_PEAK_FACTOR", "4")
    s = LimitSettings.from_env()
    assert s.model_prices == {"claude-opus-5-5": (15.0, 75.0), "x": (1.0, 2.0)} and s.full_key_duration == "365d"
    assert s.peak_factor == 4.0
    monkeypatch.setenv("FULL_KEY_DURATION", "1 year")
    with pytest.raises(ConfigError):
        LimitSettings.from_env()
    monkeypatch.setenv("FULL_KEY_DURATION", "")
    monkeypatch.setenv("BUDGET_MODEL_PRICES", "[1, 2]")
    with pytest.raises(ConfigError):
        LimitSettings.from_env()


def test_self_approval_default_follows_auth_mode(monkeypatch):
    monkeypatch.delenv("ALLOW_SELF_APPROVAL", raising=False)
    monkeypatch.setenv("AUTH_MODE", "dev")
    assert Settings.from_env().allow_self_approval is True
    monkeypatch.setenv("AUTH_MODE", "oidc")
    assert Settings.from_env().allow_self_approval is False


def test_gate_lifecycle():
    gates = domain.build_gates({"data_class": "sec-b", "external": True})
    assert gates == [{"gate": "G0", "status": "approved"}, {"gate": "G2", "status": "pending"},
                     {"gate": "G8", "status": "pending"}, {"gate": "G9", "status": "pending"}]
    assert domain.next_gate("in_review", gates) == "G2"
    gates = domain.with_gate(gates, "G2", "approved")
    assert domain.pending_review_gate(gates) == "G8"
    gates = domain.with_gate(gates, "G8", "approved")
    gates, stage = domain.launch(gates)
    assert stage == "trial" and domain.stage_of(gates) == "trial" and domain.next_gate("trial", gates) == "G9"
    gates, stage = domain.launch(domain.with_gate(gates, "G9", "approved"))
    assert stage == "full" and domain.stage_of(gates) == "full" and domain.next_gate("active", gates) is None
    simple, stage = domain.launch(domain.build_gates({"data_class": "sec-c"}))
    assert stage == "full" and simple[-1] == {"gate": "G9", "status": "approved"}
    assert domain.ref(uuid.UUID("3f2a9c41-7d1e-4c55-9a0b-2b6f1d9e8c70")) == "req-3f2a9c41"


def test_validation_normalizes():
    v = RequestValidator.load(SCHEMA)
    out = v.normalize(valid_request(team="  製造  ", alias="", data_class="sec-c", data_class_other="ignored"))
    assert out["team"] == "製造" and out["alias"] is None and out["data_class_other"] is None
    assert out["target_date"] == dt.date(2026, 12, 15) and out["cloud"] == "any" and out["unsure"] is False
    assert document_from_row(out)["target_date"] == "2026-12-15"
    unsure = v.normalize({"team": "T", "use_case": "u", "data_class": "property", "unsure": True})
    assert unsure["models"] == []
    with pytest.raises(ValidationFailed) as exc:
        v.normalize(valid_request(team="   ", models=[], extra=1, target_date="2026-02-30", rpd=1.5))
    kinds = {(tuple(e["loc"]), e["type"]) for e in exc.value.errors}
    assert {(("body",), "additionalProperties"), (("body", "team"), "minLength"), (("body", "models"), "minItems"),
            (("body", "target_date"), "format"), (("body", "rpd"), "type")} <= kinds
    with pytest.raises(ValidationFailed):
        v.normalize(valid_request(data_class="other"))                 # needs data_class_other
    with pytest.raises(ValidationFailed):
        v.normalize(["not", "an", "object"])
    long = v.errors({**valid_request(), "use_case": "x" * 5000})
    assert len(long[0]["msg"]) <= 300


def test_cursor_roundtrip():
    row = {"created_at": dt.datetime(2026, 10, 31, 6, 0, 0, 123456, tzinfo=dt.timezone.utc),
           "id": uuid.UUID("3f2a9c41-7d1e-4c55-9a0b-2b6f1d9e8c70")}
    created, rid = store.decode_cursor(store.encode_cursor(row))
    assert created == row["created_at"] and rid == row["id"]
    with pytest.raises(store.BadCursor):
        store.decode_cursor("not-a-cursor")


def test_entrypoint_config_errors(monkeypatch, capsys):
    import json
    import logging

    from request_api.__main__ import main

    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    assert main(["check-config"]) == 2
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "configuration error" and "OIDC_ISSUER" in line["error"]
    monkeypatch.setenv("AUTH_MODE", "dev")
    assert main(["check-config"]) == 0
    assert main(["bogus"]) == 2
    logging.getLogger().handlers.clear()
