"""Retrieval over the portal view: a port of the prototype page's buildIndex() / search().

Tokens: Latin words (plus their dotted/dashed parts) and CJK character bigrams. Query words expand through a
synonym map; documents score BM25-like (idf * f/(f+1.2)), title words count double, a model named in the
question triples its model page, and a month in the question ("11 月", "November") favours dated documents.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .texts import (CTX, CTX_NONE, EVENT, GATE, GATE_STATE_ZH, PAGES, SERVICE, TIER, VERDICT_ZH, avail_en, avail_zh, cloud_zh,
                    jjoin, jsnum)

__all__ = ["Doc", "Index", "toks", "SYN", "ROUTE_OK", "filter_links"]

SYN = [["aws", "bedrock", "亞馬遜"], ["azure", "foundry", "微軟", "microsoft"], ["gcp", "google", "vertex", "谷歌", "agent platform"],
       ["退役", "retire", "retirement", "retiring", "eol", "下架", "停用", "淘汰", "deprecated", "legacy"],
       ["價格", "price", "pricing", "cost", "成本", "費用", "多少錢", "便宜", "cheap", "cheapest", "貴", "漲價"],
       ["台灣", "taiwan", "台北", "taipei", "境內"], ["上架", "launch", "available", "ga", "release", "發布", "發佈", "推出", "新模型"],
       ["資安", "security", "漏洞", "cve", "攻擊", "惡意", "malware"], ["開放權重", "open", "weights", "開源", "hugging"],
       ["安全評估", "矩陣", "安全指標", "matrix", "紅隊"], ["越獄", "jailbreak", "injection", "注入"],
       ["幻覺", "hallucination", "錯誤資訊", "misinformation"], ["代理", "agent", "agents", "agentic"],
       ["檢索", "rag", "向量", "embedding", "嵌入"], ["數發部", "moda"],
       ["洩漏", "外洩", "leak", "leakage", "disclosure", "pii", "個資"], ["拒答", "refusal", "refuse"],
       ["供應鏈", "supply", "sbom"], ["申請", "request", "apply", "access", "開通"], ["測試", "test", "tests", "安全測試"],
       ["比較", "compare", "vs"]]
SYNMAP: dict[str, list[str]] = {}
for _group in SYN:
    for _w in _group:
        SYNMAP[_w] = _group

_LATIN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")
_NON_CJK = re.compile(r"[^㐀-鿿]+")
_MONTH_ZH = re.compile(r"([0-9]{1,2})\s*月")
_MONTH_EN = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.ASCII)
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_SHORT = re.compile(r"^(claude|gpt-|gemini|grok)\s*")

# internal routes a chat answer may link to (port of the page's ROUTE_OK)
ROUTE_OK = re.compile(r"^#(home|news|news-open|news-todo|retire|models|tests|cost|apply|m-[a-z0-9-]+(?:/(?:security|rollout))?|cost-[a-z0-9-]+|"
                      r"tests-[a-z0-9-]+|apply-[a-z0-9-]+|news-it-[a-f0-9]+|sec-SEC-[0-9]{2})$")
_MD_INTERNAL = re.compile(r"\[([^\]\n]{1,90})\]\((#[^)\s]*)\)")
_MD_EXTERNAL = re.compile(r"\[([^\]\n]{1,90})\]\((?!#)[^)\s]*\)")


def toks(s) -> list[str]:
    s = str(s or "").lower()
    out: list[str] = []
    for w in _LATIN.findall(s):
        out.append(w)
        if "." in w or "-" in w:
            out.extend(p for p in re.split(r"[.\-]", w) if p)
    for seg in _NON_CJK.sub(" ", s).split(" "):
        if len(seg) == 1:
            out.append(seg)
        out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return out


@dataclass
class Doc:
    route: str
    tz: str
    te: str
    text: str
    snip: str
    date: str = ""
    tf: dict[str, int] = field(default_factory=dict, repr=False)
    tt: set[str] = field(default_factory=set, repr=False)


class Index:
    """Search index for one view. Immutable once built; swap the whole object when the view changes."""

    def __init__(self, view: dict, view_id: str | None = None):
        self.view = view
        self.view_id = view_id
        self.docs: list[Doc] = []
        self.idf: dict[str, float] = {}
        self.page_titles: dict[str, tuple[str, str]] = {}
        self.models = list(view.get("models") or [])
        self.by_id = {m["id"]: m for m in self.models if isinstance(m, dict) and m.get("id")}
        self.news_ids = {n.get("id") for n in view.get("news") or []}
        self.sec_ids = {x.get("id") for x in (view.get("secMatrix") or {}).get("items") or []}
        self._build()

    # -- building ------------------------------------------------------------------------------------
    def _add(self, route, tz, te, text, snip=None, date=None):
        tz = "" if tz is None else str(tz)
        te = str(te) if te else tz
        text = "" if text is None else str(text)
        self.docs.append(Doc(route=route, tz=tz, te=te, text=text, snip=snip if snip else text[:420], date=date or ""))

    def _build(self) -> None:
        v = self.view
        security = v.get("security") or {}
        sec_models = security.get("models") or {}
        for route, tz, te, words in PAGES:
            self.page_titles[route] = (tz, te)
            self._add(route, tz, te, words, f"{tz} / {te}")
        for m in self.models:
            offs_map = m.get("offs") or {}
            parts = []
            for c in ("aws", "azure", "gcp", "direct"):
                o = offs_map.get(c)
                if not o:
                    continue
                s = cloud_zh(c) + (" " + SERVICE.get(o.get("service"), o.get("service")) if o.get("service") else "")
                s += "：" + jsnum(o.get("status"))
                if o.get("price"):
                    s += f"，${jsnum(o['price'].get('in'))} / ${jsnum(o['price'].get('out'))}"
                if c == "aws":
                    s += "，台北：" + ("可（跨區 global）" if o.get("taipei") == "global" else "不可")
                if o.get("eol"):
                    s += f"，最早退役 {o['eol']}"
                parts.append(s)
            offs = "；".join(parts)
            aa = (m.get("aa") or {}).get("score") if m.get("aa") else None
            avz, ave = avail_zh(m), avail_en(m)
            snip = (f"{m.get('name')}（{TIER['zh'].get(m.get('tier'), '')} / {TIER['en'].get(m.get('tier'), '')}）能力分數 "
                    f"{jsnum(aa) if aa is not None else '—'}。{avz}。{m.get('oneZ') or ''} {m.get('oneE') or ''} 上架：{offs}。"
                    f"優點：{'、'.join(m.get('hiZ') or [])}。要注意：{'、'.join(m.get('cauZ') or [])}。{'; '.join(m.get('cauE') or [])}。")
            text = jjoin([snip, ave, "申請 開放 上線 什麼時候 可以用 request available open when", m["id"], m.get("vendor"),
                          " ".join(str(b.get("t") or "") for b in m.get("breaking") or []),
                          " ".join(m.get("brE") or [])])
            self._add("#m-" + m["id"], m.get("name"), m.get("name"), text, snip[:700])
            sr = sec_models.get(m["id"])
            if sr:
                cnt = sr.get("counts") or {}
                issues = sr.get("issues") or []
                tested = (f"安全測試通過 {cnt.get('pass', 0)}、未通過 {cnt.get('fail', 0)}、待複核 {cnt.get('review', 0)}："
                          + "、".join(f"{i.get('t')} {i.get('v')}" for i in issues))
                summary = (f"安全測試（{security.get('date')}）通過 {cnt.get('pass', 0)}、未通過 {cnt.get('fail', 0)}"
                           f"（{'、'.join(str(i.get('t')) for i in issues if i.get('v') == 'fail')}）、待複核 {cnt.get('review', 0)}")
            else:
                tested, summary = "還沒有測試結果 not tested", "安全、相容、效能都還沒有測"
            self._add("#tests-" + m["id"], f"{m.get('name')} 測試結果", f"{m.get('name')} test results",
                      f"{m.get('name')} 測試 結果 安全 相容 效能 test results security {tested}", f"{m.get('name')} 測試結果：{summary}")
            self._add("#cost-" + m["id"], f"{m.get('name')} 成本試算", f"{m.get('name')} cost estimate",
                      f"{m.get('name')} 成本 試算 每月 費用 cost estimate monthly",
                      f"{m.get('name')} 的每月成本可在成本試算頁計算 / monthly cost estimate page")
            self._layer_docs(m, sr, security, offs_map, avz, ave)
        hl = {h.get("id"): h for h in v.get("hl") or []}
        news = []
        for n in v.get("news") or []:
            shown = n.get("date") if n.get("date") and n.get("date") <= (n.get("last") or "") else n.get("last")
            news.append((n, shown or ""))
        news.sort(key=lambda x: (x[0].get("last") or "") + x[1], reverse=True)
        for n, shown in news:
            h = hl.get(n.get("id"))
            names = " ".join(self.by_id[i]["name"] for i in n.get("models") or [] if i in self.by_id)
            text = jjoin([n.get("t"), h.get("en") if h else "", n.get("d"), n.get("sec"), " ".join(n.get("clouds") or []),
                          n.get("vendor"), names, shown])
            snip = f"{shown} {n.get('t')}{' / ' + str(h.get('en')) if h else ''}：{n.get('d')}"[:480]
            self._add("#news-" + str(n.get("id")), h.get("zh") if h else n.get("t"), h.get("en") if h else n.get("t"), text, snip)
        for r in v.get("ret") or []:
            ev_zh, ev_en = EVENT["zh"].get(r.get("ev"), r.get("ev")), EVENT["en"].get(r.get("ev"), r.get("ev"))
            svc = SERVICE.get(r.get("svc")) or r.get("svc")
            text = jjoin([r.get("name"), r.get("pmid"), cloud_zh(r.get("cloud")), svc, r.get("date"), r.get("repl"), r.get("scope"),
                          "退役 retire"])
            snip = (f"{r.get('date')} {r.get('name')}（{cloud_zh(r.get('cloud'))} {svc}）{ev_zh}"
                    f"{'，替代：' + str(r.get('repl')) if r.get('repl') else ''}")
            self._add("#retire", f"{r.get('name')}：{r.get('date')} {ev_zh}", f"{r.get('name')}: {ev_en} {r.get('date')}", text, snip,
                      r.get("date"))
        for c in list(v.get("cdNew") or []) + list(v.get("cdPrice") or []):
            self._add(c.get("link") or "#news", c.get("zh"), c.get("en"),
                      jjoin([c.get("zh"), c.get("en"), c.get("wz"), c.get("we"), c.get("date") or "日期未公布 no date"]),
                      f"{c.get('date') or '日期未公布'} {c.get('zh')} / {c.get('en')}（{c.get('wz')}）", c.get("date") or "")
        for o in v.get("open") or []:
            host = o.get("host") or {}
            self._add("#news-open", o.get("name"), o.get("name"),
                      jjoin([o.get("name"), o.get("dev"), o.get("origin"), o.get("license"), o.get("one"),
                             " ".join(cloud_zh(c) + " " + (host.get(c) or "not-offered") for c in ("aws", "azure", "gcp")),
                             "開放權重 open weights"]))
        for a in v.get("actions") or []:
            t = str(a.get("t") or "")
            self._add("#news-todo", t[:40], t[:40], f"{a.get('p')} {a.get('due') or ''} {t}")
        run_by = {i.get("id"): i for i in security.get("items") or []}
        pri_zh = {"high": "高", "medium": "中", "low": "低"}
        mx = v.get("secMatrix") or {}
        for it in mx.get("items") or []:
            r = run_by.get(it.get("id"))
            res = ""
            if r:
                res = "、".join(
                    f"{'Gateway' if k == 'gateway' else k} {VERDICT_ZH.get(x.get('v'), x.get('v'))}"
                    f"{' 失敗 ' + str(round_half_up(x['rate'] * 100)) + '%' if x.get('rate') is not None else ''}"
                    for k, x in (r.get("res") or {}).items())
            tier = "核心項目" if it.get("tier") == "core" else "擴充草案，優先序" + pri_zh.get(it.get("pri"), str(it.get("pri")))
            head = f"{it.get('id')} {it.get('n')}（{tier}；OWASP 2026：{' '.join(it.get('owasp') or []) or '—'}；適用架構：{it.get('arch')}）"
            snip = (f"{head}。測什麼：{it.get('what')} 通過標準：{it.get('pass')} 工具：{it.get('tools')} 測試單位：{it.get('unit')}。"
                    + (f"{security.get('date')} 測試（當時門檻：{r.get('rule')}）：{res}。" if r else "還沒測過。"))
            text = jjoin([head, it.get("nEn"), it.get("what"), it.get("whatEn"), it.get("pass"), it.get("passEn"), it.get("tools"),
                          it.get("users"), it.get("unit"), it.get("note") or it.get("why") or "",
                          it.get("noteEn") or it.get("whyEn") or "", " ".join(it.get("atlas") or []), " ".join(it.get("moda") or []),
                          " ".join(it.get("modaEn") or []), " ".join(it.get("other") or []), it.get("archEn"), res,
                          "安全 資安 測試 安全評估矩陣 security matrix test"])
            self._add("#sec-" + str(it.get("id")), f"{it.get('id')} {it.get('n')}", f"{it.get('id')} {it.get('nEn')}", text, snip[:640])
        reading = mx.get("reading") or []
        self._add("#tests", "安全評估矩陣（安全指標）：怎麼看", "Security matrix: how to read it",
                  jjoin([" ".join(mx.get("intro") or []), " ".join(reading), " ".join(mx.get("introEn") or []),
                         " ".join(mx.get("readingEn") or []),
                         "安全評估矩陣 安全指標 security indicators 核心 擴充草案 優先序 現役模型 三的法則 0 of n rule of three"]),
                  (f"安全評估矩陣共 32 項：SEC-01 到 SEC-16 是核心項目，SEC-17 到 SEC-32 是擴充草案（附優先序）。{' '.join(reading[:3])} "
                   f"結果欄是 {security.get('date')} 測試的判定，用當時的門檻；草案項目還沒測。")[:700])
        self._add("#apply", "申請規則：要過哪些關卡", "Request rules: which steps apply",
                  "申請 規則 關卡 法遵審查 試用 資料等級 Property Security C Security B Security A/AA 其他 對外服務 每天 請求 填表人 alias "
                  "申請單位 特殊備註 複選 request rules compliance beta data class",
                  "申請表欄位：申請單位、填表人 Alias、用途、想用的模型（可複選）、資料等級（其他（請說明）、Property、Security C、Security B、"
                  "Security A/AA）、偏好的雲、用量、希望上線日、特殊備註。資料等級是 Security B、Security A/AA、其他，或對外服務時，要過法遵審查；"
                  "對外服務或每天 10,000 次以上要先試用兩週。")
        for g in v.get("gates") or []:
            gid = g.get("id")
            self._add("#apply", f"{gid} {GATE['zh'].get(gid, gid)}", f"{gid} {GATE['en'].get(gid, gid)}",
                      jjoin([gid, g.get("name"), g.get("owner"), g.get("exit"), GATE["en"].get(gid, gid), "申請 流程 關卡 step"]))
        df: dict[str, int] = {}
        for d in self.docs:
            tf: dict[str, int] = {}
            for w in toks(d.text + " " + d.tz + " " + d.te):
                tf[w] = tf.get(w, 0) + 1
            if d.date:
                tf["@m" + d.date[5:7]] = 2
            d.tf = tf
            d.tt = set(toks(d.tz + " " + d.te))
            for w in tf:
                df[w] = df.get(w, 0) + 1
        n_docs = len(self.docs)
        self.idf = {w: math.log(1 + n_docs / n) for w, n in df.items()}

    def _layer_docs(self, m: dict, sr, security: dict, offs_map: dict, avz: str, ave: str) -> None:
        """Docs for the model page's layers 2 (#m-<id>/security) and 3 (#m-<id>/rollout)."""
        gw = m.get("gw") or {}
        prov = "、".join(str(x.get("model")) for x in gw.get("providers") or [])
        res_z = "；".join(f"{cloud_zh(c)}：{'、'.join(offs_map[c].get('residency') or []) or '—'}"
                         for c in ("aws", "azure", "gcp", "direct") if offs_map.get(c))
        tw_z = "有" if any((o or {}).get("twResidency") == "yes" for o in offs_map.values()) else "沒有"
        cnt = (sr or {}).get("counts") or {}
        sec_z = (f"安全測試（{security.get('date')}）通過 {cnt.get('pass', 0)}、未通過 {cnt.get('fail', 0)}、待複核 {cnt.get('review', 0)}"
                 if sr else "安全測試還沒測")
        name = m.get("name")
        self._add(f"#m-{m['id']}/security", f"{name} 資安與 GW", f"{name} security & gateway",
                  jjoin([f"{name} 資安 資料處理 資料地區 台灣境內處理 公司 gateway 名稱 LiteLLM 設定 data residency gateway LiteLLM",
                         res_z, prov, sec_z]),
                  (f"{name} 的資安與 GW：{sec_z}；台灣境內處理：{tw_z}；可選的資料地區：{res_z}；公司 gateway 名稱 "
                   f"{gw.get('alias') or m['id']}{'，LiteLLM：' + prov if prov else ''}")[:600])
        progress = (self.view.get("progress") or {}).get(m["id"]) or {}
        gates_z = "、".join(f"{GATE['zh'].get(g.get('id'), g.get('id'))}{GATE_STATE_ZH.get(progress.get(g.get('id')) or 'todo', '')}"
                            for g in self.view.get("gates") or [])
        self._add(f"#m-{m['id']}/rollout", f"{name} 導入細節", f"{name} rollout details",
                  jjoin([f"{name} 導入 進度 關卡 預計 開放 上線 時間 rollout progress gates", avz, ave, gates_z]),
                  f"{name} 導入進度：{avz}。關卡：{gates_z}"[:600])

    # -- search --------------------------------------------------------------------------------------
    def search(self, q: str, k: int = 8) -> list[Doc]:
        q = str(q)
        want: dict[str, float] = {}
        mz = _MONTH_ZH.search(q)
        me = _MONTH_EN.search(q.lower())
        mon = int(mz.group(1)) if mz else (MONTHS.index(me.group(1)) + 1 if me else 0)
        if 1 <= mon <= 12:
            want["@m" + f"{mon:02d}"] = 3
        for w in toks(q):
            want[w] = max(want.get(w, 0), 1)
            g = SYNMAP.get(w)
            if g:
                for x in g:
                    for y in toks(x):
                        want[y] = max(want.get(y, 0), 0.5)
        scored = []
        for d in self.docs:
            s = 0.0
            for w, wt in want.items():
                f = d.tf.get(w)
                if f:
                    s += wt * self.idf.get(w, 0) * (f / (f + 1.2)) * (2 if w in d.tt else 1)
            if s > 0:
                scored.append([d, s])
        scored.sort(key=lambda x: -x[1])
        ql = q.lower()
        for m in self.models:
            short = _SHORT.sub("", str(m.get("name") or "").lower(), count=1)
            if short and short in ql:
                for x in scored:
                    if x[0].route == "#m-" + m["id"]:
                        x[1] *= 3
        scored.sort(key=lambda x: -x[1])
        seen, out = set(), []
        for d, _ in scored:
            key = d.route + "|" + d.tz
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
            if len(out) >= k:
                break
        return out

    # -- helpers for answers -------------------------------------------------------------------------
    @staticmethod
    def context(hits: list[Doc]) -> str:
        lines = [f"- [{h.tz}{' / ' + h.te if h.te != h.tz else ''}]({h.route})：{h.snip}" for h in hits]
        return CTX + "\n" + ("\n".join(lines) if lines else CTX_NONE)

    def title(self, d: Doc, lang: str) -> str:
        pg = self.page_titles.get(d.route)
        if pg:
            return pg[1] if lang == "en" else pg[0]
        return (d.te or d.tz) if lang == "en" else d.tz

    def links(self, hits: list[Doc], lang: str, limit: int = 4) -> list[dict]:
        seen, out = set(), []
        for h in hits:
            if h.route in seen or not self.route_allowed(h.route):
                continue
            seen.add(h.route)
            out.append({"route": h.route, "title": self.title(h, lang)})
            if len(out) >= limit:
                break
        return out

    def route_allowed(self, route: str) -> bool:
        """ROUTE_OK plus: the model, news item or matrix item the route names exists in this view."""
        if not ROUTE_OK.match(route):
            return False
        name = route[1:]
        if name.startswith("m-"):
            return name[2:].split("/", 1)[0] in self.by_id
        for prefix in ("cost-", "tests-", "apply-"):
            if name.startswith(prefix):
                return name[len(prefix):] in self.by_id
        if name.startswith("news-it-"):
            return name[5:] in self.news_ids
        if name.startswith("sec-"):
            return name[4:] in self.sec_ids
        return True


def round_half_up(x: float) -> int:
    """Math.round for the percentages the page prints."""
    return int(math.floor(x + 0.5))


def filter_links(text: str, index: Index) -> str:
    """Markdown-lite answer: keep [text](#route) only for allowed internal routes; any other link becomes plain text."""
    def internal(m: re.Match) -> str:
        return m.group(0) if index.route_allowed(m.group(2)) else m.group(1)

    text = _MD_INTERNAL.sub(internal, text or "")
    return _MD_EXTERNAL.sub(lambda m: m.group(1), text)
