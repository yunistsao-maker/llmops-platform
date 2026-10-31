"""Prompt rules for the chat and the form helper (ports of the prototype page's rules() and formRules()),
and the sanitizer for the helper's suggested form (port of sanitizeForm(), output in request-schema names)."""
from __future__ import annotations

import datetime as dt
import json
import math
import re

from .texts import STATUS_ZH, TIER, avail_zh, cloud_zh, jsnum

__all__ = ["chat_rules", "form_rules", "sanitize_form", "lowest_price", "tier_sort_key", "taiwan_note", "DATA_CLASSES"]

DATA_CLASSES = ("other", "property", "sec-c", "sec-b", "sec-a")
CLOUDS = ("aws", "azure", "gcp")
TIER_ORDER = {"frontier": 0, "balanced": 1, "efficient": 2}
PAGES = ("#home 總覽 Overview、#news 動態 News、#news-open 開放權重模型 Open-weight models、#news-todo 待辦 To-dos、"
         "#retire 退役日曆 Retirements、#models 模型目錄 Models、#m-<模型 id> 模型頁（能力與能不能申請）Model page、"
         "#m-<模型 id>/security 模型的資安與 GW、#m-<模型 id>/rollout 模型的導入細節（進度與預計開放時間）、#tests 測試結果 Tests、"
         "#tests-<模型 id> 某個模型的測試結果、#cost 成本試算 Cost、#cost-<模型 id> 某個模型的成本試算、#apply 申請 Request、"
         "#sec-SEC-01 到 #sec-SEC-32 安全評估矩陣的某一項 Security matrix item")
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{1,80}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
LIMITS = {"rpd": 100_000_000, "tokens_in": 10_000_000, "tokens_out": 10_000_000}  # request.schema.json maxima


def lowest_price(m: dict):
    best = None
    for o in (m.get("offs") or {}).values():
        p = (o or {}).get("price")
        if p and p.get("in") is not None and p.get("out") is not None:
            if best is None or p["in"] + p["out"] < best["in"] + best["out"]:
                best = p
    return best


def tier_sort_key(m: dict):
    return (TIER_ORDER.get(m.get("tier"), 3), -((m.get("aa") or {}).get("score") or 0))


def taiwan_note(view: dict) -> str:
    """Which catalog models process data in Taiwan (computed; the prototype had this sentence hard-coded)."""
    models = view.get("models") or []
    tw = [m["name"] for m in models if any((o or {}).get("twResidency") == "yes" for o in (m.get("offs") or {}).values())]
    if tw:
        return f"可以在台灣境內處理的模型：{'、'.join(tw)}。"
    jp = [m["name"] for m in models
          if any(str(r).upper().startswith("JP") for r in ((m.get("offs") or {}).get("aws") or {}).get("residency") or [])]
    near = f"（最接近的是 Bedrock 的日本區：{'、'.join(jp)}）" if jp else ""
    return f"目錄裡沒有任何模型在台灣境內處理{near}。"


def chat_rules(view: dict, today: dt.date, lang: str) -> str:
    return f"""你是「LLMOps Portal」的問答小幫手。這個 portal 整理 AWS、Azure、Google Cloud 與模型原廠的最新模型消息：新模型上架、價格、退役、資安、內部測試與申請流程。
規則：
1. 只根據每個問題後面附的「portal 資料」回答；資料沒寫的價格、日期、上架狀態不要自己補，直接說 portal 裡沒有，並建議最相關的頁面。
2. 用使用者提問的語言回答（繁體中文或英文）。先給結論，用白話，2 到 5 句或簡短條列，不要用表格。
3. 最後附上 1 到 3 個最相關的頁面連結，格式一定是 [頁面名稱](#路由)，路由只能用 portal 資料裡出現過的。
4. 日期寫 YYYY-MM-DD；價格寫「$輸入 / $輸出，每百萬 tokens（USD）」。
5. 今天是 {today.isoformat()}；portal 資料日期是 {view.get('asOf')}。算「還有幾天」用今天的日期。
6. 問到模型什麼時候可以申請或上線，用 portal 資料裡的「申請狀態」回答，並說明預計時間是依導入關卡估算，不是承諾。
可用的頁面：{PAGES}。
現在介面語言：{'English' if lang == 'en' else '繁體中文'}。"""


def _catalog_lines(view: dict) -> str:
    sec_models = (view.get("security") or {}).get("models") or {}
    lines = []
    for m in sorted(view.get("models") or [], key=tier_sort_key):
        offs = m.get("offs") or {}
        on = []
        for c in CLOUDS:
            o = offs.get(c)
            if not o:
                continue
            s = cloud_zh(c) + " " + STATUS_ZH.get(o.get("status"), str(o.get("status")))
            if c == "aws":
                s += "（台北可經跨區 global 呼叫）" if o.get("taipei") == "global" else "（台北不可呼叫）"
            on.append(s)
        avail = "、".join(on) or "三雲都沒有"
        p = lowest_price(m)
        s = sec_models.get(m.get("id"))
        aa = (m.get("aa") or {}).get("score") if m.get("aa") else None
        lines.append(
            f"- {m.get('id')}｜{m.get('name')}｜{TIER['zh'].get(m.get('tier'), m.get('tier'))}｜能力分數 {jsnum(aa) if aa is not None else '—'}｜"
            f"每百萬 tokens ${jsnum(p['in']) if p else '?'} / ${jsnum(p['out']) if p else '?'}｜{avail_zh(m).removeprefix('申請狀態：')}｜"
            f"{avail}{'、原廠 API' if offs.get('direct') else ''}｜"
            f"{m.get('oneZ') or ''}｜要注意：{'；'.join(m.get('cauZ') or [])}｜"
            f"{'安全測試未通過 ' + str((s.get('counts') or {}).get('fail', 0)) + ' 項' if s else '安全測試還沒測'}")
    return "\n".join(lines)


def _snapshot(form: dict) -> str:
    snap = {"申請單位": form.get("team", ""), "用途": form.get("use_case", ""), "已選模型": form.get("models", []),
            "資料等級": form.get("data_class", ""), "資料等級說明": form.get("data_class_other", ""),
            "偏好的雲": form.get("cloud", "any"), "每天請求數": form.get("rpd"), "希望上線日": form.get("target_date"),
            "每次輸入tokens": form.get("tokens_in"), "每次輸出tokens": form.get("tokens_out"),
            "對外服務": form.get("external", False), "必須台灣境內處理": form.get("taiwan_only", False), "特殊備註": form.get("note", "")}
    return json.dumps(snap, ensure_ascii=False, separators=(",", ":"))


def form_rules(view: dict, form: dict, today: dt.date, lang: str) -> str:
    return f"""你是 LLMOps Portal 的「申請表小幫手」。根據使用者描述的使用情境，建議適合的模型，並幫忙填好申請表的其他欄位。
規則：
1. 模型只能從下面「模型目錄」挑，用 id，最多 3 個，依適合度排序；理由要根據目錄裡的事實（能力分數、價格、哪朵雲有、台北能不能用、要注意的事、安全測試）。
2. 資訊不夠時先給暫定建議，並在 questions 問最多 2 個最關鍵的問題（例如誰會用、資料多敏感、一天大概多少次）。
3. dataClass 只能是 "other"、"property"、"sec-c"、"sec-b"、"sec-a"（Security A/AA）或 ""。資料等級由使用者依公司分類決定：沒把握就填 ""，並在 reply 提醒使用者自己確認。
4. 沒把握的欄位填 null（數字、布林、日期）或 ""（文字），不要亂猜。use 用一兩句白話整理使用者的描述。cloud 只能是 "any"、"aws"、"azure"、"gcp"。
5. 用使用者的語言回覆（繁體中文或英文；目前介面語言：{'English' if lang == 'en' else '繁體中文'}）。reply 白話、3 到 6 句，說明推薦哪些模型與原因。
6. 只回一個 JSON 物件，不要其他文字：{{"reply": string, "questions": string[], "form": {{"models": string[], "use": string, "dataClass": string, "dataClassOther": string, "cloud": string, "rpd": number|null, "tin": number|null, "tout": number|null, "external": boolean|null, "taiwan": boolean|null, "date": string|null, "note": string}}}}
申請規則：資料等級 Security B、Security A/AA、其他，或對外服務要過法遵審查；對外服務或每天 10,000 次以上要先試用兩週。{taiwan_note(view)}
今天是 {today.isoformat()}。
模型目錄：
{_catalog_lines(view)}
使用者目前填的申請表（JSON）：{_snapshot(form)}"""


def _number(v):
    if isinstance(v, bool) or v is None or v == "":
        return None
    if isinstance(v, str):
        try:
            v = float(v.strip())
        except ValueError:
            return None
    if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return int(math.floor(v + 0.5))  # Math.round


def _text(v, limit: int) -> str | None:
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def sanitize_form(f, model_ids: set[str]) -> dict:
    """The model's suggested form -> a partial request in contracts/schemas/request.schema.json names."""
    o: dict = {}
    if not isinstance(f, dict):
        return o
    if isinstance(f.get("models"), list):
        ms = []
        for x in f["models"]:
            x = str(x)
            if x in model_ids and MODEL_ID.match(x) and x not in ms:
                ms.append(x)
        if ms:
            o["models"] = ms[:3]
    if (use := _text(f.get("use"), 300)) is not None:
        o["use_case"] = use
    if f.get("dataClass") in DATA_CLASSES:
        o["data_class"] = f["dataClass"]
        if o["data_class"] == "other" and (other := _text(f.get("dataClassOther"), 120)) is not None:
            o["data_class_other"] = other
    if f.get("cloud") in ("any",) + CLOUDS:
        o["cloud"] = f["cloud"]
    for src, dst in (("rpd", "rpd"), ("tin", "tokens_in"), ("tout", "tokens_out")):
        n = _number(f.get(src))
        if n is not None and n <= LIMITS[dst]:
            o[dst] = n
    if isinstance(f.get("external"), bool):
        o["external"] = f["external"]
    if isinstance(f.get("taiwan"), bool):
        o["taiwan_only"] = f["taiwan"]
    if isinstance(f.get("date"), str) and DATE.match(f["date"]):
        try:
            dt.date.fromisoformat(f["date"])
            o["target_date"] = f["date"]
        except ValueError:
            pass
    if (note := _text(f.get("note"), 300)) is not None:
        o["note"] = note
    return o
