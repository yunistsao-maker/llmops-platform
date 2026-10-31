"""Cases from the datasets bucket, converted back to secsuite's internal case format, and seeded sampling
(ported from tools/secsuite/fetch.py sample / sample_item).

Same dataset version + same seed + same n -> the same cases for every route and every run, so routes (and a candidate
and its baseline) are compared on identical questions.
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
from collections import OrderedDict

from llmops_common.queue import PermanentError
from llmops_common.storage import NotFound

from .scoring import SCORERS
from .twtext import ZhTw

ZHTW_FILES = ("STCharacters.txt", "TSCharacters.txt", "TWPhrases.txt")


def to_internal(case: dict, sec_id: str, dataset_id: str) -> dict:
    """Platform case {id, prompt | turns, expected?, meta} -> secsuite case (what scoring.py reads)."""
    meta = case.get("meta") or {}
    kind = "multi" if case.get("turns") else (meta.get("kind") or "chat")
    if meta.get("messages"):
        messages = meta["messages"]
    elif case.get("prompt") is not None:
        messages = [{"role": "user", "content": case["prompt"]}]
    else:
        messages = []
    c = {"id": str(case["id"]), "dataset": meta.get("source") or dataset_id, "item": sec_id,
         "category": str(meta.get("category") or "all"), "lang": meta.get("lang"), "kind": kind, "system": meta.get("system"),
         "messages": messages, "tools": meta.get("tools"), "expect": dict(case.get("expected") or {}),
         "max_tokens": int(meta.get("max_tokens") or 0), "scorer": meta.get("scorer"), "share": meta.get("share") or 1,
         "assets": meta.get("assets")}
    if kind == "multi":
        c["turns"] = list(case["turns"])
    if kind == "pair":
        c["messages_b"] = [{"role": "user", "content": meta.get("prompt_b") or ""}]
    if c["scorer"] in ("harm", "harm_multi") and not c["expect"].get("request"):
        c["expect"]["request"] = case.get("prompt") or (case.get("turns") or [""])[-1]
    return c


def sample(cases: list[dict], n: int, seed: int) -> list[dict]:
    """Seeded, stratified by category (round-robin). Same seed -> same cases for every model and re-run."""
    if n >= len(cases):
        return list(cases)
    rng = random.Random(seed)
    if cases and cases[0].get("expect", {}).get("pair"):  # bilingual pairs are sampled together
        pairs: dict[str, list[dict]] = {}
        for c in cases:
            pairs.setdefault(c["expect"]["pair"], []).append(c)
        keys = sorted(pairs)
        rng.shuffle(keys)
        out: list[dict] = []
        for k in keys:
            if len(out) + len(pairs[k]) > n:
                break
            out += pairs[k]
        return out
    groups: dict[str, list[dict]] = {}
    for c in cases:
        groups.setdefault(c["category"], []).append(c)
    keys = sorted(groups)
    for k in keys:
        rng.shuffle(groups[k])
    rng.shuffle(keys)
    out, i = [], 0
    while len(out) < n:
        took = False
        for k in keys:
            if i < len(groups[k]) and len(out) < n:
                out.append(groups[k][i])
                took = True
        if not took:
            break
        i += 1
    return out


def sample_item(cases: list[dict], n: int, seed: int) -> list[dict]:
    """n cases for one SEC item, split across its source datasets by their share (one source: plain sample)."""
    groups: dict[str, list[dict]] = {}
    for c in cases:
        groups.setdefault(c["dataset"], []).append(c)
    if not groups or n <= 0:
        return []
    ds = list(groups)
    shares = [float(groups[d][0].get("share") or 1) for d in ds]
    total = sum(shares)
    quotas = [max(1, round(n * s / total)) for s in shares]
    while sum(quotas) > n and max(quotas) > 1:
        quotas[quotas.index(max(quotas))] -= 1
    while sum(quotas) < n:
        quotas[quotas.index(min(quotas))] += 1
    out: list[dict] = []
    for d, q in zip(ds, quotas):
        out += sample(groups[d], q, seed)
    return out[:n] if len(out) > n else out


class CaseSource:
    """Reads (and caches) dataset versions from the datasets bucket."""

    def __init__(self, store, bucket_name: str, cache_size: int = 8):
        self.store, self.bucket, self.cache_size = store, bucket_name, cache_size
        self._cache: OrderedDict = OrderedDict()
        self._zh: dict[str, ZhTw] = {}
        self._lock = threading.Lock()

    @staticmethod
    def prefix(sec_id: str, dataset_id: str, version: int) -> str:
        return f"{sec_id}/{dataset_id}/v{int(version)}/"

    def load(self, sec_id: str, dataset_id: str, version: int) -> list[dict]:
        """Internal cases of one dataset version. PermanentError when the version is missing or corrupt."""
        key = (sec_id, dataset_id, int(version))
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        prefix = self.prefix(sec_id, dataset_id, version)
        try:
            body = self.store.get_bytes(self.bucket, prefix + "cases.jsonl")
        except NotFound as exc:
            raise PermanentError(f"dataset {prefix}cases.jsonl not found") from exc
        manifest = self.store.get_json(self.bucket, prefix + "manifest.json", default=None)
        if manifest and manifest.get("sha256") and manifest["sha256"] != hashlib.sha256(body).hexdigest():
            raise PermanentError(f"dataset {prefix}cases.jsonl does not match the sha256 in its manifest")
        cases = [to_internal(json.loads(line), sec_id, dataset_id) for line in body.decode("utf-8").splitlines() if line.strip()]
        unknown = sorted({str(c["scorer"]) for c in cases if c["scorer"] not in SCORERS})
        if unknown:
            raise PermanentError(f"dataset {prefix} has cases with unknown scorers {unknown}")
        if any(c["scorer"] == "zhtw" for c in cases):
            zh = self.zhtw(prefix)
            for c in cases:
                if c["scorer"] == "zhtw":
                    c["_zh"] = zh
        with self._lock:
            self._cache[key] = cases
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return cases

    def zhtw(self, prefix: str) -> ZhTw | None:
        """OpenCC tables stored as assets of the dataset version (None when absent: zhtw cases become review)."""
        with self._lock:
            if prefix in self._zh:
                return self._zh[prefix]
        try:
            texts = [self.store.get_bytes(self.bucket, f"{prefix}assets/{name}").decode("utf-8") for name in ZHTW_FILES]
        except NotFound:
            return None
        zh = ZhTw(*texts)
        with self._lock:
            self._zh[prefix] = zh
        return zh
