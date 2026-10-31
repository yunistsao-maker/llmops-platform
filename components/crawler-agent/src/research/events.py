"""Map Strands stream events to the compact events the entrypoint streams to callers.

Raw Strands events carry live objects (the agent, metrics, spans) and token-by-token tool-input
deltas; callers only need: text the model wrote, which tool was called with what, and what came
back.  Reasoning text is never forwarded.
"""

from __future__ import annotations

import json
from typing import Any

_MAX = 160


def _short(v: Any, limit: int = _MAX) -> Any:
    if isinstance(v, str):
        return v if len(v) <= limit else v[:limit] + "…"
    if isinstance(v, list):
        return [_short(x, limit) for x in v[:8]] + (["…"] if len(v) > 8 else [])
    if isinstance(v, dict):
        return {k: _short(x, limit) for k, x in list(v.items())[:12]}
    return v


def brief_input(name: str, tool_input: Any) -> Any:
    if name == "submit_proposal" and isinstance(tool_input, dict):
        p = tool_input.get("proposal")
        if isinstance(p, dict):
            return {"ops": len(p.get("ops") or []), "newSources": len(p.get("newSources") or []), "digest": bool(p.get("digest")),
                    "unverified": len(p.get("unverified") or [])}
    return _short(tool_input)


def brief_result(name: str, text: str) -> dict:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        first = (text or "").split("\n", 1)[0]
        return {"text": _short(first, 200), "chars": len(text or "")}
    if not isinstance(data, dict):
        return {"value": _short(data)}
    if "error" in data:
        return {"error": _short(data["error"], 240)}
    if name == "submit_proposal":
        return {"accepted": data.get("accepted"), "errors": len(data.get("errors", [])), "warnings": len(data.get("warnings", []))}
    keep = ("ok", "pageId", "evidenceId", "tier", "title", "count", "chars", "status", "cached", "code", "total")
    out = {k: _short(data[k]) for k in keep if k in data}
    return out or {"keys": list(data)[:8]}


class EventMapper:
    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    def map(self, ev: Any) -> list[dict]:
        if not isinstance(ev, dict):
            return []
        out: list[dict] = []
        data = ev.get("data")
        if isinstance(data, str) and data and not ev.get("reasoning"):
            out.append({"type": "text", "delta": data})
        msg = ev.get("message")
        if isinstance(msg, dict):
            for block in msg.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if "toolUse" in block:
                    tu = block["toolUse"]
                    self._names[tu.get("toolUseId", "")] = tu.get("name", "")
                    out.append({"type": "tool_call", "id": tu.get("toolUseId"), "name": tu.get("name"),
                                "input": brief_input(tu.get("name", ""), tu.get("input"))})
                elif "toolResult" in block:
                    tr = block["toolResult"]
                    text = "".join(c.get("text", "") for c in tr.get("content", []) if isinstance(c, dict))
                    name = self._names.get(tr.get("toolUseId", ""), "")
                    out.append({"type": "tool_result", "id": tr.get("toolUseId"), "name": name,
                                "ok": tr.get("status") != "error", "result": brief_result(name, text)})
        return out


# ---------------------------------------------------------------------------------------------
# rendering hint
# ---------------------------------------------------------------------------------------------
def portal_line(manifest: Any) -> str:
    """Which portal data this run reads - said up front, because a proposal made against the wrong data is useless."""
    m = manifest if isinstance(manifest, dict) else {}
    kind = m.get("kind")
    if kind == "release":
        return f"portal data: release {m.get('releaseId')}（{m.get('source')}）"
    if kind == "sample":
        return "portal data: 內建的 SAMPLE 快照 —— 沒有設定 PORTAL_DATA_URI；依它產生的提案不能發布"
    return f"portal data: {kind or '?'}" + (f"（{m.get('source')}）" if m.get("source") else "")


def display_text(ev: dict) -> str | None:
    """A short human-readable line for an event (None = nothing to show).

    ``agentcore invoke`` and the console playground only print text found at
    ``event.contentBlockDelta.delta.text``; ``with_display`` puts it there so a person watching a run
    sees progress while programs keep reading the structured ``type`` fields.
    """
    from .render import render_result_text

    t = ev.get("type")
    if t == "text":
        return ev.get("delta") or None
    if t == "run_started":
        b = ev.get("budgets") or {}
        return (f"▶ run {ev.get('runId')} · {ev.get('provider')}/{ev.get('model')} · search: {ev.get('search')} · "
                f"budget {b.get('searches')} searches / {b.get('fetches')} fetches\n  {portal_line(ev.get('portal'))}\n")
    if t == "tool_call":
        return f"\n→ {ev.get('name')} {json.dumps(ev.get('input'), ensure_ascii=False)[:220]}\n"
    if t == "tool_result":
        r = ev.get("result")
        body = json.dumps(r, ensure_ascii=False)[:200] if r else ""
        return f"  {'✓' if ev.get('ok') else '✗'} {body}\n"
    if t == "accepted":
        return f"✔ {ev.get('message', 'accepted')}\n"
    if t == "result":
        return render_result_text(ev)
    return None


def with_display(ev: dict) -> dict:
    text = display_text(ev)
    if not text or "event" in ev:
        return ev
    return {**ev, "event": {"contentBlockDelta": {"delta": {"text": text}}}}
