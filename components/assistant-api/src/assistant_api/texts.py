"""UI words the retrieval index and the prompts reuse (same strings as the portal page's T dictionary)."""
from __future__ import annotations

TIER = {"zh": {"frontier": "旗艦", "balanced": "主力", "efficient": "輕量"},
        "en": {"frontier": "Flagship", "balanced": "Everyday", "efficient": "Lightweight"}}
STATUS_ZH = {"ga": "已上架", "preview": "預覽", "private-preview": "限定預覽", "restricted": "受限", "not-offered": "未上架",
             "unknown": "未確認"}
EVENT = {"zh": {"retirement": "退役", "legacy": "進入舊版", "deprecation": "宣布停用", "extended-access": "延長使用",
                "eol-no-sooner-than": "最早退役日"},
         "en": {"retirement": "Retirement", "legacy": "Moves to legacy", "deprecation": "Deprecated",
                "extended-access": "Extended access", "eol-no-sooner-than": "Earliest retirement"}}
VERDICT_ZH = {"pass": "通過", "fail": "未通過", "review": "待複核", "info": "只記錄"}
GATE_STATE_ZH = {"done": "完成", "doing": "進行中", "blocked": "未通過", "todo": "未開始"}
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
GATE = {"zh": {"G0": "登記", "G1": "研究", "G2": "法遵審查", "G3": "相容測試", "G4": "效能測試", "G5": "品質評測", "G6": "安全測試",
               "G7": "成本審查", "G8": "試用", "G9": "上線", "G10": "退役"},
        "en": {"G0": "Intake", "G1": "Research", "G2": "Compliance", "G3": "Compatibility", "G4": "Performance",
               "G5": "Quality", "G6": "Security", "G7": "Cost review", "G8": "Beta", "G9": "Launch", "G10": "Retirement"}}
SERVICE = {"bedrock": "Bedrock", "foundry": "Foundry", "agent-platform": "Agent Platform", "anthropic-api": "Anthropic API",
           "openai-api": "OpenAI API", "gemini-api": "Gemini API"}
CTX = "portal 資料（只用這些回答）："
CTX_NONE = "（沒有找到相關資料）"

# (route, zh title, en title, search words)
PAGES = [
    ("#home", "總覽：本期重點、倒數、模型目錄", "Overview: highlights, countdown, model catalog",
     "總覽 本期 重點 倒數 最新 消息 overview highlights countdown latest"),
    ("#news", "動態：所有發布與變更", "News: every release and change", "動態 新聞 發布 變更 news updates releases changes"),
    ("#news-open", "開放權重模型", "Open-weight models", "開放權重 開源 open weights hugging face"),
    ("#news-todo", "待辦事項", "To-dos", "待辦 要做 todo action"),
    ("#retire", "退役日曆", "Retirement calendar", "退役 下架 停用 淘汰 retire retirement calendar"),
    ("#models", "模型目錄與比較", "Model catalog and comparison",
     "模型 目錄 比較 最便宜 最強 分數 models catalog compare cheapest best score"),
    ("#tests", "測試結果", "Test results", "測試 安全 效能 相容 test results security performance compatibility"),
    ("#cost", "成本試算", "Cost estimate", "成本 試算 費用 每月 多少錢 預算 cost estimate monthly budget calculator"),
    ("#apply", "申請使用模型", "Request a model", "申請 流程 表單 怎麼 開通 request access apply form steps"),
]


def cloud_zh(cloud: str) -> str:
    return {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud"}.get(cloud, "原廠 API")


def jsnum(v) -> str:
    """Numbers printed the way the page's JavaScript prints them (4.0 -> "4")."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v)


def jjoin(parts, sep: str = " ") -> str:
    """Array.prototype.join: null and undefined become empty strings."""
    return sep.join("" if p is None else str(p) for p in parts)


def avail(m: dict) -> dict:
    return m.get("avail") or {"state": "evaluating", "eta": None, "next_gate": None, "basis": "gates"}


def _part(day: str) -> int:
    d = int(day)
    return 0 if d <= 10 else 1 if d <= 20 else 2


def avail_zh(m: dict) -> str:
    """Availability as index / prompt text (port of the page's availZh: the year is always written out)."""
    a = avail(m)
    g = GATE["zh"].get(a.get("next_gate"), str(a.get("next_gate"))) if a.get("next_gate") else ""
    basis = "平台團隊提供的時間" if a.get("basis") == "editorial" else "依導入關卡估算"
    eta = a.get("eta")
    when = f"{int(eta[:4])} 年 {int(eta[5:7])} 月{['上旬', '中旬', '下旬'][_part(eta[8:10])]}" if eta else ""
    state = a.get("state")
    if state == "available":
        return "申請狀態：可以申請"
    if state == "paused":
        return f"申請狀態：暫停導入（{g}沒有通過），還沒有預計時間"
    if state == "trial":
        return "申請狀態：試用中" + (f"，預計 {when}正式開放（{basis}）" if when else "")
    return "申請狀態：尚未開放，" + (f"預計 {when}可以申請（{basis}{'；下一關：' + g if g else ''}）" if when else "還沒有預計時間")


def avail_en(m: dict) -> str:
    a = avail(m)
    g = GATE["en"].get(a.get("next_gate"), str(a.get("next_gate"))) if a.get("next_gate") else ""
    eta = a.get("eta")
    when = f"{['early', 'mid', 'late'][_part(eta[8:10])]} {MONTHS_EN[int(eta[5:7]) - 1]} {int(eta[:4])}" if eta else ""
    state = a.get("state")
    if state == "available":
        return "Availability: ready to request"
    if state == "paused":
        return f"Availability: on hold ({g} did not pass), no date yet"
    if state == "trial":
        return "Availability: in beta" + (f", general availability expected {when}" if when else "")
    return "Availability: not open yet" + (f", expected {when}" if when else ", no date yet")
