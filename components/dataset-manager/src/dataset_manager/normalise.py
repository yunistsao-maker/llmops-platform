"""Turn cases into the platform format of contracts/storage-layout.md and validate internal golden sets.

Platform case (one JSON object per line of cases.jsonl):
    {"id": "...", "prompt": "..." | "turns": ["...", ...], "expected": {...}?, "meta": {...}}

meta carries what test-runner needs to replay the secsuite logic:
    scorer      which secsuite scorer judges the answer (harm, cse, toolcall, canary, needle, regexrule, agentstep,
                amplify, mcq, overrefusal, refusalsignal, stance, zhtw, poison, marker, harm_multi)
    source      the source dataset (a composite holds several), share = its weight when sampling
    category, lang, kind ("chat" | "multi" | "pair"), system, tools, max_tokens
    messages    full OpenAI-style message list when the case is more than one user message (pre-filled tool call)
    prompt_b    the variant prompt of a "pair" case (SEC-07 token amplification)
    assets      files the scorer needs, stored under <version>/assets/ (zhtw: the OpenCC dictionaries)
"""
from __future__ import annotations

import re

SCORERS = ("harm", "harm_multi", "cse", "toolcall", "canary", "needle", "regexrule", "agentstep", "amplify", "mcq",
           "overrefusal", "refusalsignal", "stance", "zhtw", "poison", "marker")

# Scorer for an internal golden set when neither meta.scorer nor the expected fields decide it (secsuite's mapping).
DEFAULT_SCORER = {
    "SEC-01": "harm", "SEC-02": "cse", "SEC-03": "canary", "SEC-04": "needle", "SEC-05": "regexrule", "SEC-06": "agentstep",
    "SEC-07": "amplify", "SEC-08": "mcq", "SEC-09": "harm", "SEC-10": "overrefusal", "SEC-11": "refusalsignal",
    "SEC-12": "stance", "SEC-13": "harm_multi", "SEC-14": "poison", "SEC-15": "harm",
}

SENSITIVITY = ("none", "low", "medium", "medium-high", "high", "highest")
SEC_ID = re.compile(r"^SEC-\d{2}$")
DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")


class CaseError(ValueError):
    """A golden-set line cannot be used."""


def sensitivity_rank(value: str) -> int:
    return SENSITIVITY.index(value) if value in SENSITIVITY else len(SENSITIVITY)


def to_platform(case: dict, source: dict) -> dict:
    """secsuite internal case -> platform case. `source` is the manifest entry of the dataset it came from."""
    meta: dict = {"source": source["id"], "scorer": source["scorer"], "share": source.get("share", 1),
                  "category": str(case.get("category") or "all"), "lang": case.get("lang") or "en", "kind": case.get("kind") or "chat"}
    if case.get("system"):
        meta["system"] = case["system"]
    if case.get("tools"):
        meta["tools"] = case["tools"]
    if case.get("max_tokens"):
        meta["max_tokens"] = int(case["max_tokens"])
    out: dict = {"id": str(case["id"])}
    if meta["kind"] == "multi":
        out["turns"] = list(case["turns"])
    else:
        messages = case.get("messages") or []
        users = [m for m in messages if m.get("role") == "user"]
        out["prompt"] = users[0]["content"] if users else ""
        if len(messages) != 1 or messages[0].get("role") != "user":
            meta["messages"] = messages
        if meta["kind"] == "pair":
            meta["prompt_b"] = case["messages_b"][0]["content"]
    if case.get("expect"):
        out["expected"] = case["expect"]
    out["meta"] = meta
    return out


def infer_scorer(sec_id: str, case: dict) -> str | None:
    meta = case.get("meta") or {}
    if meta.get("scorer"):
        return meta["scorer"]
    e = case.get("expected") or {}
    if case.get("turns"):
        return "harm_multi"
    if meta.get("kind") == "pair" or meta.get("prompt_b"):
        return "amplify"
    for key, scorer in (("forbidden_tools", "toolcall"), ("canary", "canary"), ("rule", "regexrule"), ("harmful", "agentstep"),
                        ("answer", "mcq"), ("wrong", "poison"), ("marker", "marker"), ("flags", "stance"),
                        ("judge_question", "cse")):
        if key in e:
            return scorer
    if "needle" in e:
        return "cse" if sec_id in ("SEC-01", "SEC-02") else "needle"
    return DEFAULT_SCORER.get(sec_id)


def normalise_golden(sec_id: str, dataset_id: str, raw: dict, lineno: int) -> dict:
    """Validate one line of an internal JSONL golden set and return it in platform form."""
    where = f"line {lineno}"
    if not isinstance(raw, dict):
        raise CaseError(f"{where}: not a JSON object")
    if raw.get("id") is None or str(raw["id"]).strip() == "":
        raise CaseError(f"{where}: missing id")
    turns, prompt = raw.get("turns"), raw.get("prompt")
    if turns is not None:
        if not isinstance(turns, list) or not turns or not all(isinstance(t, str) and t.strip() for t in turns):
            raise CaseError(f"{where}: turns must be a non-empty list of strings")
    elif not isinstance(prompt, str) or not prompt.strip():
        raise CaseError(f"{where}: needs prompt (string) or turns (list of strings)")
    expected = raw.get("expected") or {}
    meta = dict(raw.get("meta") or {})
    if not isinstance(expected, dict) or not isinstance(meta, dict):
        raise CaseError(f"{where}: expected and meta must be objects")
    scorer = infer_scorer(sec_id, {**raw, "meta": meta, "expected": expected})
    if scorer not in SCORERS:
        raise CaseError(f"{where}: no scorer for {sec_id}; set meta.scorer to one of {', '.join(SCORERS)}")
    if scorer == "zhtw":
        raise CaseError(f"{where}: the zhtw scorer needs the OpenCC dictionaries; import the public zhtw-terms dataset instead")
    kind = "multi" if turns is not None else ("pair" if scorer == "amplify" else meta.get("kind") or "chat")
    if scorer == "harm_multi" and turns is None:
        raise CaseError(f"{where}: harm_multi needs turns")
    if scorer in ("harm", "harm_multi"):
        expected.setdefault("request", prompt if turns is None else turns[-1])
    needs = {"cse": ("needle", "judge_question"), "toolcall": ("forbidden_tools",), "canary": ("canary",), "needle": ("needle",),
             "regexrule": ("rule",), "agentstep": ("harmful",), "mcq": ("answer",), "poison": ("wrong", "right")}
    if scorer == "cse":
        if not (expected.get("needle") or expected.get("judge_question")):
            raise CaseError(f"{where}: cse needs expected.needle or expected.judge_question")
    elif scorer in needs:
        missing = [k for k in needs[scorer] if expected.get(k) in (None, "", [])]
        if missing:
            raise CaseError(f"{where}: {scorer} needs expected.{', expected.'.join(missing)}")
    if scorer == "regexrule":
        try:
            re.compile(expected["rule"])
        except re.error as exc:
            raise CaseError(f"{where}: expected.rule is not a valid regular expression: {exc}") from exc
    if scorer in ("toolcall", "agentstep") and not meta.get("tools"):
        raise CaseError(f"{where}: {scorer} needs meta.tools (OpenAI-style tool definitions)")
    if scorer == "amplify" and not (isinstance(meta.get("prompt_b"), str) and meta["prompt_b"].strip()):
        raise CaseError(f"{where}: amplify needs meta.prompt_b (the variant prompt)")
    if scorer == "mcq":
        expected.setdefault("letters", "ABCD")
    meta.update(scorer=scorer, source=meta.get("source") or dataset_id, share=meta.get("share") or 1,
                category=str(meta.get("category") or "all"), lang=meta.get("lang") or "zh-TW", kind=kind)
    out: dict = {"id": str(raw["id"]).strip()}
    if turns is not None:
        out["turns"] = turns
    else:
        out["prompt"] = prompt
    if expected:
        out["expected"] = expected
    out["meta"] = meta
    return out
