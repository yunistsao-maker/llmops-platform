import pytest

from notifier.messages import duration_zh, render, when
from notifier.settings import ConfigError, Settings

S = Settings(reviewers={"G2": ("legal@example.com",), "G8": ("beta@example.com",), "G9": ("beta@example.com",)},
             admins=("ops@example.com",), request_url="https://portal.example/#/requests/{request_id}",
             model_url="https://portal.example/#/model/{model_id}",
             gateway_url="https://llm.example", key_help_url="https://wiki.example/llm-keys")
RID = "3f2a9c41-7d1e-4c55-9a0b-2b6f1d9e8c70"
BASE = {"request_id": RID, "ref": "req-3f2a9c41", "team": "智慧製造處", "alias": "amy", "requester_email": "amy@example.com"}


def test_submitted_goes_to_next_gate_reviewers():
    msgs = render("request.submitted", {**BASE, "gates": ["G0", "G2", "G8", "G9"], "next_gate": "G2",
                                        "models": ["claude-sonnet-5-5"], "data_class": "other",
                                        "data_class_other": "匿名化摘要", "rpd": 12000, "external": True,
                                        "taiwan_only": False}, S)
    assert len(msgs) == 1 and msgs[0].to == ("legal@example.com",) and msgs[0].webhook
    m = msgs[0]
    assert m.subject == "【LLMOps】待審：req-3f2a9c41 智慧製造處（法遵審查（G2）） | Review needed: req-3f2a9c41 (G2 Compliance)"
    for text in ("法遵審查（G2）有一筆模型申請等你審查。", "資料等級：其他：匿名化摘要", "每天請求數：12,000",
                 "對外服務：是；限台灣境內處理：否", "審查流程：法遵審查（G2） → 試用（G8） → 上線（G9）",
                 f"審查頁面：https://portal.example/#/requests/{RID}"):
        assert text in m.body
    assert render("request.submitted", {**BASE, "next_gate": None}, S) == []
    again = render("request.submitted", {**BASE, "next_gate": "G2", "revision": 2}, S)[0]
    assert "重新送出" in again.body


def test_decided_messages():
    ok = render("request.decided", {**BASE, "gate": "G2", "decision": "approve", "comment": "資料留存 30 天",
                                    "status": "in_review", "next_gate": "G8"}, S)
    assert [m.audience for m in ok] == ["requester", "reviewers"]
    assert ok[0].to == ("amy@example.com",) and not ok[0].webhook and "下一步：試用（G8）。" in ok[0].body
    assert ok[1].to == ("beta@example.com",) and "輪到試用（G8）審查" in ok[1].body
    last = render("request.decided", {**BASE, "gate": "G8", "decision": "approve", "status": "provisioning"}, S)
    assert len(last) == 1 and "開通金鑰" in last[0].body
    no = render("request.decided", {**BASE, "gate": "G2", "decision": "reject", "comment": "資料不能出境",
                                    "actor": "legal@example.com", "status": "rejected"}, S)[0]
    assert "未通過法遵審查（G2）" in no.body and "原因：資料不能出境" in no.body and "rejected" in no.subject
    fix = render("request.decided", {**BASE, "gate": "G8", "decision": "request_changes", "comment": "請補用量",
                                     "status": "changes_requested"}, S)[0]
    assert fix.subject.startswith("【LLMOps】req-3f2a9c41 需要補件") and "修改頁面：" in fix.body


def test_approved_and_key_messages_never_contain_a_key():
    appr = render("request.approved", {**BASE, "stage": "full", "models": ["claude-sonnet-5-5"], "budget_usd": 3713.0,
                                       "rpm": 35, "tpm": 122500, "duration": None, "budget_duration": "30d"}, S)[0]
    assert "正在開通正式金鑰" in appr.body and "每 30 天 US$ 3,713" in appr.body and "金鑰不會自動到期" in appr.body
    trial = render("request.approved", {**BASE, "stage": "trial", "budget_usd": 1733, "duration": "14d"}, S)[0]
    assert "試用期間 US$ 1,733" in trial.body and "金鑰 14 天後到期" in trial.body
    prov = render("key.provisioned", {**BASE, "stage": "trial", "key_alias": "req-3f2a9c41-trial",
                                      "secret_ref": "vault:secret/llmops/keys/req-3f2a9c41-trial",
                                      "fetch_hint": "vault kv get -mount=secret -field=api_key llmops/keys/req-3f2a9c41-trial",
                                      "expires_at": "2026-11-14T06:37:31Z", "models": ["claude-sonnet-5-5"]}, S)
    assert [m.audience for m in prov] == ["requester", "reviewers"]
    body = prov[0].body
    assert "金鑰存放位置：vault:secret/llmops/keys/req-3f2a9c41-trial" in body and "取用方式：vault kv get" in body
    assert "到期時間：2026-11-14 14:37（台北時間）" in body and "Gateway：https://llm.example" in body
    assert "不要貼到 email" in body and "sk-" not in body
    assert "G9" in prov[1].body and prov[1].to == ("beta@example.com",)


def test_admin_messages():
    failed = render("key.failed", {**BASE, "stage": "full", "error": "key generation failed: HTTP 503"}, S)[0]
    assert failed.to == ("ops@example.com",) and f"/api/requests/{RID}/reprovision" in failed.body
    rel = render("release.published", {"release_id": "20261031T000000Z", "previous_release_id": "20261030T000000Z",
                                       "held": True, "changes": {"routes_added": [{"route_id": f"gw:onprem:m{i}"} for i in range(10)],
                                                                 "prices_changed": [], "retirements": ["claude-x"]}}, S)[0]
    assert "暫停" in rel.subject and "新增路線 10、價格變動 0、退役 1" in rel.body and "等 10 項" in rel.body
    gw = render("gateway.version_changed", {"gateway": "onprem", "previous_version": "1.105.1", "version": "1.105.2"}, S)[0]
    assert "1.105.1 → 1.105.2" in gw.subject
    run = render("test.run.completed", {"plan_id": "p1", "run_id": "20261031T061346-a55616", "status": "partial"}, S)[0]
    assert "部分完成" in run.subject and run.webhook
    assert render("route.added", {"model_id": "x"}, S) == []


def test_formatting_and_english_toggle():
    assert duration_zh("14d") == "14 天" and duration_zh("1mo") == "1 個月" and duration_zh(None) is None
    assert when("2026-11-14T16:00:00Z", S).startswith("2026-11-15 00:00")
    zh_only = Settings(english_subject=False, admins=("ops@example.com",))
    assert " | " not in render("key.failed", {**BASE, "stage": "trial"}, zh_only)[0].subject


def test_settings_defaults(monkeypatch):
    for k in ("SMTP_HOST", "WEBHOOK_URLS", "NOTIFY_DRY_RUN", "SMTP_FROM", "NOTIFY_REVIEWERS_G9"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NOTIFY_REVIEWERS_G8", "a@example.com, b@example.com")
    s = Settings.from_env()
    assert s.dry_run and s.reviewers["G9"] == ("a@example.com", "b@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    with pytest.raises(ConfigError):
        Settings.from_env()                               # SMTP_FROM missing
    monkeypatch.setenv("SMTP_FROM", "LLMOps <llmops@example.com>")
    s = Settings.from_env()
    assert not s.dry_run and s.smtp_port == 587 and s.smtp_starttls
    monkeypatch.setenv("NOTIFY_REQUEST_URL", "https://portal.example/requests")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_entrypoint_config_errors(monkeypatch, capsys):
    import json
    import logging

    from notifier.__main__ import main

    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.delenv("SMTP_FROM", raising=False)
    assert main(["check-config"]) == 2
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["msg"] == "configuration error" and "SMTP_FROM" in line["error"]
    assert main(["bogus"]) == 2
    logging.getLogger().handlers.clear()


def test_model_status_changed_messages():
    base = {"model_id": "gpt-6-luna", "model_name": "GPT-6 Luna", "from": "evaluating"}
    trial = render("model.status_changed", {**base, "to": "trial", "eta": "2026-12-01"}, S)
    assert len(trial) == 1 and trial[0].audience == "subscribers" and trial[0].to == () and not trial[0].webhook
    m = trial[0]
    assert m.subject == "【LLMOps】GPT-6 Luna 開放試用申請 | GPT-6 Luna is open for trial requests"
    for text in ("你訂閱的模型 GPT-6 Luna（gpt-6-luna）已開放試用", "狀態：評估中 → 試用", "預計時程：2026-12-01",
                 "模型頁面：https://portal.example/#/model/gpt-6-luna", "取消訂閱"):
        assert text in m.body
    ga = render("model.status_changed", {"model_id": "gpt-6-luna", "from": "trial", "to": "available",
                                         "eta": "2026-12-01T01:00:00Z"}, S)[0]
    assert ga.subject.startswith("【LLMOps】gpt-6-luna 開放申請 | gpt-6-luna can now be requested")
    assert "已正式開放" in ga.body and "試用 → 可申請" in ga.body and "2026-12-01 09:00（台北時間）" in ga.body
    for to in ("paused", "retired", "watch", "evaluating", None, "bogus"):
        assert render("model.status_changed", {**base, "to": to}, S) == []
    plain = Settings()
    no_link = render("model.status_changed", {**base, "to": "available"}, plain)[0]
    assert "模型頁面：" not in no_link.body and "https:" not in no_link.body


def test_model_url_setting(monkeypatch):
    for k in ("SMTP_HOST", "WEBHOOK_URLS", "NOTIFY_REQUEST_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NOTIFY_MODEL_URL", "https://portal.example/#/model/{model_id}")
    assert Settings.from_env().model_url == "https://portal.example/#/model/{model_id}"
    monkeypatch.setenv("NOTIFY_MODEL_URL", "https://portal.example/#/models")
    with pytest.raises(ConfigError):
        Settings.from_env()
