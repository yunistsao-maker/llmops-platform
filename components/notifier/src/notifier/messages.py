"""Message texts (Traditional Chinese, optional English subject suffix). Pure functions: event -> messages.

Each message names its audience; `webhook` marks messages that may also go to team chat (never the
requester's personal messages). No message ever contains a key: key.provisioned carries only where the key
is stored (secret_ref) and how to fetch it (fetch_hint).
Audience "subscribers" is a template: the handler sends one copy to every subscriber of the model
(requests.subscriptions), each with its own `key` for deduplication.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from llmops_common.gates import GATE_NAMES

from .settings import Settings

DATA_CLASS = {"other": "其他", "property": "Property", "sec-c": "Security C", "sec-b": "Security B",
              "sec-a": "Security A/AA"}
STAGE = {"trial": "試用", "full": "正式"}
RUN_STATUS = {"done": "完成", "partial": "部分完成", "failed": "失敗"}
MODEL_STATUS = {"watch": "觀察中", "evaluating": "評估中", "trial": "試用", "available": "可申請", "paused": "暫停",
                "retired": "已退役"}
NOTIFY_SUBSCRIBERS_ON = ("trial", "available")          # paused / retired (and earlier stages) send nothing
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNITS = {"s": "秒", "m": "分鐘", "h": "小時", "d": "天", "w": "週", "mo": "個月"}
_DURATION = re.compile(r"^(\d+)(mo|s|m|h|d|w)$")


@dataclass(frozen=True)
class Message:
    audience: str              # reviewers | requester | admins | subscribers (template) | subscriber
    to: tuple[str, ...]        # email recipients (may be empty: then only webhooks, if allowed)
    subject: str
    body: str
    webhook: bool = False
    key: str | None = None     # dedupe discriminator (default: the message's position)


# -- small formatters -----------------------------------------------------------------------------------
def gate_label(gate: str | None) -> str:
    if not gate:
        return ""
    zh = GATE_NAMES.get(gate, (gate, gate))[0]
    return f"{zh}（{gate}）"


def duration_zh(value: str | None) -> str | None:
    m = _DURATION.match(value or "")
    return f"{m.group(1)} {_UNITS[m.group(2)]}" if m else value


def when(value: str | None, s: Settings) -> str | None:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(s.tz).strftime("%Y-%m-%d %H:%M") + f"（{s.tz_label}）"


def _yn(value) -> str:
    return "是" if value else "否"


def _models(data: dict) -> str:
    models = [m for m in data.get("models") or [] if m]
    if models:
        return "、".join(models)
    return "不確定（請審查人建議）" if data.get("unsure") else "未指定"


def _ref(data: dict) -> str:
    if data.get("ref"):
        return str(data["ref"])
    rid = str(data.get("request_id") or "")
    return "req-" + rid.replace("-", "")[:8] if rid else "（未知申請）"


def _subject(s: Settings, zh: str, en: str | None = None) -> str:
    return f"{s.subject_prefix}{zh}" + (f" | {en}" if s.english_subject and en else "")


def _link(s: Settings, data: dict, label: str = "申請頁面") -> str | None:
    rid = data.get("request_id")
    return f"{label}：{s.request_url.format(request_id=rid)}" if s.request_url and rid else None


def _body(*lines) -> str:
    return "\n".join(str(line) for line in lines if line)


def _short(item) -> str:
    if isinstance(item, dict):
        for k in ("route_id", "id", "model_id", "model", "name"):
            if item.get(k):
                return str(item[k])
        return ", ".join(f"{k}={v}" for k, v in list(item.items())[:3])
    return str(item)


def _list(items, limit: int = 8) -> str | None:
    items = list(items or [])
    if not items:
        return None
    shown = "、".join(_short(i) for i in items[:limit])
    return shown + (f" 等 {len(items)} 項" if len(items) > limit else "")


# -- per-event renderers --------------------------------------------------------------------------------
def _review_request(s: Settings, data: dict, gate: str, intro: str) -> Message:
    team, ref = data.get("team") or "", _ref(data)
    cls = DATA_CLASS.get(data.get("data_class"), data.get("data_class") or "未填")
    if data.get("data_class") == "other" and data.get("data_class_other"):
        cls += f"：{data['data_class_other']}"
    flow = " → ".join(gate_label(g) for g in data.get("gates") or [] if g != "G0")
    rpd = data.get("rpd")
    body = _body(
        intro,
        f"申請單位：{team}",
        data.get("alias") and f"填表人：{data['alias']}",
        f"模型：{_models(data)}",
        f"資料等級：{cls}",
        rpd is not None and f"每天請求數：{int(rpd):,}",
        "external" in data and f"對外服務：{_yn(data.get('external'))}；限台灣境內處理：{_yn(data.get('taiwan_only'))}",
        flow and f"審查流程：{flow}",
        _link(s, data, "審查頁面"),
    )
    en = GATE_NAMES.get(gate, (gate, gate))[1]
    return Message("reviewers", tuple(s.reviewers.get(gate, ())),
                   _subject(s, f"待審：{ref} {team}（{gate_label(gate)}）", f"Review needed: {ref} ({gate} {en})"),
                   body, webhook=True)


def request_submitted(s: Settings, data: dict) -> list[Message]:
    gate = data.get("next_gate")
    if not gate:          # nothing to review: request.approved follows
        return []
    again = int(data.get("revision") or 1) > 1
    intro = f"{gate_label(gate)}有一筆模型申請等你審查" + ("（申請人已修改後重新送出）。" if again else "。")
    return [_review_request(s, data, gate, intro)]


def request_decided(s: Settings, data: dict) -> list[Message]:
    gate, decision, ref = data.get("gate"), data.get("decision"), _ref(data)
    team, comment, nxt = data.get("team") or "", data.get("comment"), data.get("next_gate")
    gname = GATE_NAMES.get(gate, (gate, gate))[0]
    to = (data["requester_email"],) if data.get("requester_email") else ()
    out: list[Message] = []
    if decision == "approve":
        if data.get("status") == "provisioning":
            step = "下一步：開通金鑰，完成後會再通知你。"
        elif nxt:
            step = f"下一步：{gate_label(nxt)}。"
        else:
            step = None
        out.append(Message("requester", to, _subject(s, f"{ref} 已通過{gname}", f"{ref}: {gate} approved"),
                           _body(f"你的模型申請（{team}）已通過{gate_label(gate)}。", step,
                                 comment and f"審查意見：{comment}", _link(s, data))))
        if nxt in s.reviewers and data.get("status") == "in_review":
            out.append(_review_request(s, data, nxt, f"{ref} 已通過{gate_label(gate)}，輪到{gate_label(nxt)}審查。"))
    elif decision == "reject":
        out.append(Message("requester", to, _subject(s, f"{ref} 未通過{gname}", f"{ref}: request rejected"),
                           _body(f"你的模型申請（{team}）未通過{gate_label(gate)}。", comment and f"原因：{comment}",
                                 data.get("actor") and f"如有疑問請聯絡審查人：{data['actor']}", _link(s, data))))
    elif decision == "request_changes":
        out.append(Message("requester", to, _subject(s, f"{ref} 需要補件", f"{ref}: changes requested"),
                           _body(f"{gate_label(gate)}審查人請你修改申請內容後重新送出。", comment and f"說明：{comment}",
                                 _link(s, data, "修改頁面"))))
    return out


def request_approved(s: Settings, data: dict) -> list[Message]:
    stage, ref, team = data.get("stage"), _ref(data), data.get("team") or ""
    stage_zh = STAGE.get(stage, stage or "")
    limits = []
    if data.get("budget_usd") is not None:
        period = "試用期間" if stage == "trial" else (f"每 {duration_zh(data.get('budget_duration'))}"
                                                   if data.get("budget_duration") else "總額")
        limits.append(f"{period} US$ {float(data['budget_usd']):,.0f}")
    if data.get("rpm"):
        limits.append(f"每分鐘 {int(data['rpm']):,} 次請求")
    if data.get("tpm"):
        limits.append(f"{int(data['tpm']):,} tokens")
    expiry = (f"金鑰 {duration_zh(data['duration'])}後到期。" if data.get("duration") else "金鑰不會自動到期。")
    to = (data["requester_email"],) if data.get("requester_email") else ()
    return [Message("requester", to,
                    _subject(s, f"{ref} 已核准，正在開通{stage_zh}金鑰", f"{ref}: approved, provisioning {stage} key"),
                    _body(f"你的模型申請（{team}）已核准，正在開通{stage_zh}金鑰。", f"模型：{_models(data)}",
                          limits and "額度：" + "，".join(limits) + "。", expiry,
                          "金鑰開通後會再通知你取用方式。", _link(s, data)))]


def key_provisioned(s: Settings, data: dict) -> list[Message]:
    stage, ref, team = data.get("stage"), _ref(data), data.get("team") or ""
    stage_zh = STAGE.get(stage, stage or "")
    expires = when(data.get("expires_at"), s)
    to = (data["requester_email"],) if data.get("requester_email") else ()
    out = [Message("requester", to, _subject(s, f"{ref} {stage_zh}金鑰已開通", f"{ref}: {stage} key ready"),
                   _body(f"你的{stage_zh}金鑰已開通，名稱：{data.get('key_alias')}。",
                         f"金鑰存放位置：{data.get('secret_ref')}",
                         data.get("fetch_hint") and f"取用方式：{data['fetch_hint']}",
                         data.get("models") and f"可用模型：{_models(data)}",
                         f"到期時間：{expires}" if expires else "金鑰不會自動到期。",
                         s.gateway_url and f"Gateway：{s.gateway_url}（OpenAI 相容 API）",
                         "請把金鑰設成服務的 Secret 使用；不要貼到 email、聊天室、工單或程式碼裡。",
                         s.key_help_url and f"使用說明：{s.key_help_url}"))]
    if stage == "trial":
        out.append(Message("reviewers", tuple(s.reviewers.get("G9", ())),
                           _subject(s, f"{ref} 試用開始", f"{ref}: trial started"),
                           _body(f"{team} 的試用金鑰已開通（{data.get('key_alias')}）" +
                                 (f"，試用到 {expires}。" if expires else "。"),
                                 f"試用結束前請決定是否正式上線：{gate_label('G9')}核准或否決。",
                                 _link(s, data, "審查頁面")), webhook=True))
    return out


def key_failed(s: Settings, data: dict) -> list[Message]:
    ref, team = _ref(data), data.get("team") or ""
    stage_zh = STAGE.get(data.get("stage"), "")
    return [Message("admins", s.admins, _subject(s, f"{ref} 金鑰開通失敗", f"{ref}: key provisioning failed"),
                    _body(f"申請 {ref}（{team}）的{stage_zh}金鑰開通失敗。", f"錯誤：{data.get('error') or '未知'}",
                          f"排除問題後，管理員可以呼叫 POST /api/requests/{data.get('request_id')}/reprovision 重試。",
                          _link(s, data)), webhook=True)]


def release_published(s: Settings, data: dict) -> list[Message]:
    rel, held = data.get("release_id"), bool(data.get("held"))
    changes = data.get("changes") or {}
    added, prices, retired = (changes.get(k) or [] for k in ("routes_added", "prices_changed", "retirements"))
    return [Message("admins", s.admins,
                    _subject(s, f"資料發布 {rel}" + ("（暫停，待確認）" if held else ""),
                             f"Release {rel} published" + (" (held)" if held else "")),
                    _body(f"portal 資料已發布：{rel}（上一版：{data.get('previous_release_id') or '無'}）。",
                          f"新增路線 {len(added)}、價格變動 {len(prices)}、退役 {len(retired)}。",
                          held and "這次發布被暫停（held），需要人工確認後才會上線。",
                          _list(added) and f"新增路線：{_list(added)}",
                          _list(retired) and f"退役：{_list(retired)}"), webhook=True)]


def gateway_version_changed(s: Settings, data: dict) -> list[Message]:
    gw, prev, ver = data.get("gateway") or "gateway", data.get("previous_version") or "未知", data.get("version")
    return [Message("admins", s.admins, _subject(s, f"Gateway 版本變更：{gw} {prev} → {ver}",
                                                f"Gateway {gw} {prev} -> {ver}"),
                    _body(f"{gw} 的版本從 {prev} 變成 {ver}。", "相關安全測試會重新排程；結果出來前請留意異常。"),
                    webhook=True)]


def test_run_completed(s: Settings, data: dict) -> list[Message]:
    run, status = data.get("run_id"), data.get("status")
    zh = RUN_STATUS.get(status, status or "未知")
    return [Message("admins", s.admins, _subject(s, f"安全測試{zh}：{run}", f"Test run {run}: {status}"),
                    _body(f"安全測試 {run} 已結束，狀態：{zh}。", data.get("plan_id") and f"測試計畫：{data['plan_id']}",
                          status != "done" and "有測試工作沒有完成，請查看 test-orchestrator 的紀錄。"), webhook=True)]


def model_status_changed(s: Settings, data: dict) -> list[Message]:
    to, model_id = data.get("to"), str(data.get("model_id") or "")
    if to not in NOTIFY_SUBSCRIBERS_ON:
        return []
    name = data.get("model_name") or model_id
    before = MODEL_STATUS.get(data.get("from"), data.get("from") or "未知")
    eta = data.get("eta")
    if eta:
        eta = eta if _DATE.match(str(eta)) else when(str(eta), s)
    if to == "trial":
        subject = _subject(s, f"{name} 開放試用申請", f"{name} is open for trial requests")
        first = f"你訂閱的模型 {name}（{model_id}）已開放試用，現在可以提出試用申請。"
    else:
        subject = _subject(s, f"{name} 開放申請", f"{name} can now be requested")
        first = f"你訂閱的模型 {name}（{model_id}）已正式開放，現在可以提出申請。"
    body = _body(first, f"狀態：{before} → {MODEL_STATUS[to]}", eta and f"預計時程：{eta}",
                 s.model_url and model_id and f"模型頁面：{s.model_url.format(model_id=model_id)}",
                 "不想再收到這類通知，可以在模型頁面取消訂閱。")
    return [Message("subscribers", (), subject, body)]


RENDERERS = {
    "request.submitted": request_submitted,
    "request.decided": request_decided,
    "request.approved": request_approved,
    "key.provisioned": key_provisioned,
    "key.failed": key_failed,
    "release.published": release_published,
    "gateway.version_changed": gateway_version_changed,
    "test.run.completed": test_run_completed,
    "model.status_changed": model_status_changed,
}


def render(event: str, data: dict, s: Settings) -> list[Message]:
    renderer = RENDERERS.get(event)
    return renderer(s, data or {}) if renderer else []
