"""Human-readable renderings of a run: the pull-request body (zh-TW) and a short text summary.

Everything quoted from the web is untrusted text.  It is only ever shown inside code spans / fenced
blocks, with length limits, so a page cannot inject markdown, images or links into a PR description.
"""

from __future__ import annotations

import json
import re
from typing import Any

_TIER_ZH = {"official": "官方", "independent": "獨立第三方", "community": "社群", "derived": "推導", "unverified": "未驗證"}
_WS = re.compile(r"\s+")


def _flat(text: Any, limit: int = 160) -> str:
    s = _WS.sub(" ", str(text if text is not None else "")).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def code(text: Any, limit: int = 160) -> str:
    """Inline code span that cannot be broken out of (and cannot hold a table separator)."""
    s = _flat(text, limit).replace("|", "¦").replace("`", "'")
    return f"`{s}`" if s else "—"


_RISKY = re.compile(r"[\[\]<>`!|*_~#\\&]|https?://|www\.", re.I)


def prose(text: Any, limit: int = 300) -> str:
    """Untrusted prose for a markdown line: plain when it is plain, otherwise a code span (nothing in it is interpreted)."""
    flat = _flat(text, limit)
    return code(flat, limit) if _RISKY.search(flat) else flat


def link(url: Any) -> str:
    """A fetched URL, percent-encoding the characters that could end a markdown/HTML construct."""
    return re.sub(r"[<>\"'`\s()\[\]|\\]", lambda m: "%%%02X" % ord(m.group()), str(url))[:300]


def _value(v: Any, limit: int = 140) -> str:
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return code(json.dumps(v, ensure_ascii=False, separators=(",", ":")), limit)
    return code(v, limit)


def fence(text: str, limit: int = 600) -> str:
    body = _flat(text, limit)
    ticks = "```"
    while ticks in body:
        ticks += "`"
    return f"{ticks}text\n{body}\n{ticks}"


def _tier(t: Any) -> str:
    return _TIER_ZH.get(t) or code(t, 30)


def render_pr_body(proposal: dict, result: dict | None = None) -> str:
    """Markdown for the pull request that applies ``proposal`` to the portal repo's ``data/`` folder."""
    p = proposal
    ops = p.get("ops") or []
    ev = p.get("evidence") or {}
    lines: list[str] = []
    add = lines.append

    add(f"## Research agent proposal {code(p.get('id', '?'), 60)}")
    add("")
    add(f"> 由 AgentCore research agent 於 {prose(p.get('createdAt', '?'), 40)} 產生。**所有變更都需要人工複核**（對照下方證據原文與來源頁面）；"
        "agent 沒有改動任何 `platformStatus`，也沒有寫入內部測試結果。")
    sample = (p.get("portal") or {}).get("sample")
    if sample:
        add("")
        add("> ⚠️ 這份 proposal 是根據 agent 內建的 **SAMPLE** portal 資料產生，不是你的實際 repo；合併前請對照真實檔案。")
    base = (p.get("portal") or {}).get("manifest") or {}
    if base.get("commit") or base.get("syncedAt"):
        add("")
        add(f"> 基準資料：{code(base.get('source', '?'), 80)}"
            + (f" · commit {code(str(base['commit'])[:10], 12)}" if base.get("commit") else "")
            + (f" · 同步於 {prose(base['syncedAt'], 30)}" if base.get("syncedAt") else "")
            + ("（同步時工作目錄有未 commit 的變更）" if base.get("dirty") else ""))
    add("")
    add("### 摘要")
    add(prose(p.get("summary"), 1500) if p.get("summary") else "（無）")
    add("")

    task = p.get("task") or {}
    add(f"- 任務：{code(task.get('kind', '?'))}" + (f"，指示：{code(task['instruction'], 200)}" if task.get("instruction") else ""))
    add(f"- 變更數：{len(ops)} 個 op"
        + (f"、新增來源 {len(p['newSources'])} 個" if p.get("newSources") else "")
        + ("、含 weekly digest" if p.get("digest") else ""))
    val = p.get("validation") or {}
    add(f"- 驗證：portal schema/cross-file 問題 {val.get('portalProblemsBefore', '?')} → {val.get('portalProblemsAfter', '?')}"
        "（只計新增的問題）")
    add("")

    if ops:
        add("### 變更明細")
        add("")
        add("| # | 檔案 | 操作 | 路徑 | 原值 | 新值 | 證據 |")
        add("|---|---|---|---|---|---|---|")
        for i, op in enumerate(ops, 1):
            add(f"| {i} | {code(op['file'], 60)} | {op['op']} | {code(op['path'], 90)} | {_value(op.get('before'))} | "
                f"{_value(op.get('value')) if op['op'] != 'remove' else '（移除）'} | {', '.join(op.get('evidence', [])) or '—'} |")
        add("")
        for i, op in enumerate(ops, 1):
            add(f"{i}. {prose(op.get('rationale'), 300)}")
        add("")

    if p.get("newSources"):
        add("### 新增來源（sources.json）")
        for s in p["newSources"]:
            add(f"- {code(s.get('id'))} [{_tier(s.get('type', ''))}] {code(s.get('title'), 100)} — {link(s.get('url'))}")
        add("")
    if p.get("touchSources"):
        add("### 更新 `accessed` 的既有來源")
        add(", ".join(code(s) for s in p["touchSources"]))
        add("")

    if p.get("digest"):
        d = p["digest"]
        add(f"### Weekly digest {code(d.get('id'), 40)}（{prose(d.get('periodStart'), 20)} → {prose(d.get('periodEnd'), 20)}）")
        add(prose(d.get("headline"), 400))
        counts = {k: len(v) for k, v in d.items() if isinstance(v, list) and v}
        add("")
        add("項目數：" + "、".join(f"{prose(k, 40)} {n}" for k, n in counts.items()))
        add("")

    if ev:
        add("### 證據（原文引用，已確認逐字出現在抓取到的頁面）")
        add("")
        for eid, e in ev.items():
            add(f"**{eid}** · {_tier(e.get('tier', ''))}（{prose(e.get('publisher'), 60)}）· 抓取於 {prose(e.get('fetchedAt', '?'), 40)}"
                + ("（瀏覽器渲染）" if e.get("rendered") else ""))
            add(f"- 頁面：{prose(e.get('title'), 120)} — {link(e.get('url'))}")
            add(fence(e.get("quote", "")))
            if e.get("note"):
                add(f"- 備註：{prose(e['note'], 200)}")
            add("")

    if p.get("checked"):
        add("### 已檢查（無變更）")
        for c in p["checked"]:
            add(f"- {prose(c.get('topic'), 100)}：{prose(c.get('result'), 200)}")
        add("")
    if p.get("unverified"):
        add("### 未能確認")
        for u in p["unverified"]:
            add(f"- {prose(u.get('claim'), 240)} — {prose(u.get('reason'), 240)}")
        add("")
    warnings = val.get("warnings") or []
    if warnings:
        add("### 驗證器警告（請特別留意）")
        for w in warnings:
            add(f"- {prose(w, 300)}")
        add("")

    stats = p.get("stats") or {}
    if stats:
        add(f"<sub>searches {stats.get('searches', '?')} · fetches {stats.get('fetches', '?')} · evidence {stats.get('evidence', '?')}"
            + (f" · run {result.get('runId')}" if result and result.get("runId") else "") + "</sub>")
    add("")
    add("合併前請執行 `npm run validate`；merge 後由 Amplify 重新建置。")
    return "\n".join(lines).rstrip() + "\n"


def render_result_text(result: dict) -> str:
    """The short text a CLI shows for the final result event."""
    status = result.get("status")
    out = [""]
    if status == "accepted":
        p = result.get("proposal") or {}
        ops = p.get("ops") or []
        out.append(f"✔ proposal {p.get('id')} 通過驗證（{len(ops)} 個 op"
                   + (f"、{len(p['newSources'])} 個新來源" if p.get("newSources") else "") + ("、含 digest" if p.get("digest") else "") + "）")
        out.append(_flat(p.get("summary"), 600))
        for i, op in enumerate(ops[:12], 1):
            out.append(f"  {i}. {op['op']} {op['file']}{op['path']}"
                       + (f"  {_flat(op.get('before'), 40)} → {_flat(op.get('value'), 60)}" if op["op"] == "replace" else ""))
        if len(ops) > 12:
            out.append(f"  … 另有 {len(ops) - 12} 個")
        for w in ((p.get("validation") or {}).get("warnings") or [])[:5]:
            out.append(f"  ⚠ {_flat(w, 200)}")
    elif status == "no_proposal":
        out.append("✘ 沒有產出通過驗證的 proposal：" + _flat(result.get("error"), 300))
        rep = result.get("report") or {}
        for e in (rep.get("errors") or [])[:6]:
            out.append(f"  - {_flat(e, 240)}")
        out.append(f"  已保留 {len(result.get('evidence') or [])} 筆證據在結果 JSON。")
    else:
        out.append("✘ 執行失敗：" + _flat(result.get("error"), 400))
    if result.get("saved"):
        out.append(f"結果已儲存：{result['saved']}")
    if result.get("saveError"):
        out.append(f"⚠ 儲存結果失敗：{_flat(result['saveError'], 200)}")
    out.append(f"（run {result.get('runId')} · {result.get('elapsedSeconds')}s）")
    return "\n".join(out) + "\n"
