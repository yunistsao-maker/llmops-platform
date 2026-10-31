"""Import public secsuite datasets and internal golden sets into the datasets bucket.

Public datasets come from secsuite_manifest.json (tools/secsuite/manifest.json, 23 datasets). Three ways to name one:
    <dataset id>   e.g. jbb-harmful   -> dataset `jbb-harmful` under its SEC item
    SEC-xx         e.g. SEC-01        -> composite `secsuite-sec-01`: every manifest dataset of that item, each case keeping
                                         its source and share, so test-runner samples them in secsuite's proportions
    all            every SEC item as a composite

index.json holds one dataset per SEC item (the contract); a composite is how an item keeps several public sources.
Every import needs a named approver: several datasets hold harmful requests in clear text.
"""
from __future__ import annotations

import json
import re
from importlib import resources
from typing import Callable

import httpx

from llmops_common.ids import iso
from llmops_common.log import get_logger, kv

from . import adapters, normalise
from .layout import DatasetBucket, sha256_hex

log = get_logger("dataset_manager")
TOOL = "dataset-manager/0.1.0 (secsuite adapters 0.1.0)"
ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,99}$")


class ImportRefused(Exception):
    """The import may not proceed (no approver, unknown dataset, invalid file)."""


def load_manifest() -> dict:
    return json.loads(resources.files(__package__).joinpath("secsuite_manifest.json").read_text(encoding="utf-8"))


def datasets_by_id(manifest: dict | None = None) -> dict[str, dict]:
    return {d["id"]: d for d in (manifest or load_manifest())["datasets"]}


def composite_id(sec_id: str) -> str:
    return "secsuite-" + sec_id.lower()


def check_approver(approved_by: str | None) -> str:
    if not approved_by or not approved_by.strip():
        raise ImportRefused("refused: --approved-by <alias> is required. Public security datasets contain harmful "
                            "requests in clear text; a named person must approve each import.")
    alias = approved_by.strip()
    if not ALIAS.match(alias):
        raise ImportRefused(f"refused: --approved-by must be a person's alias (letters, digits, . _ @ + -), got {alias!r}")
    return alias


def resolve(name: str, manifest: dict | None = None) -> list[tuple[str, str, list[dict]]]:
    """name -> [(sec_id, dataset_id, [manifest entries])]."""
    manifest = manifest or load_manifest()
    by_id = datasets_by_id(manifest)
    if name == "all":
        items = sorted({d["item"] for d in manifest["datasets"]})
        return [(i, composite_id(i), [d for d in manifest["datasets"] if d["item"] == i]) for i in items]
    if normalise.SEC_ID.match(name.upper()):
        sec = name.upper()
        parts = [d for d in manifest["datasets"] if d["item"] == sec]
        if not parts:
            raise ImportRefused(f"no public dataset for {sec} in the secsuite manifest")
        return [(sec, composite_id(sec), parts)]
    if name.startswith("secsuite-sec-"):
        return resolve(name.removeprefix("secsuite-").upper(), manifest)
    if name not in by_id:
        raise ImportRefused(f"unknown dataset {name!r}; see `list`")
    d = by_id[name]
    return [(d["item"], d["id"], [d])]


class Downloader:
    """HTTPS downloads with a size cap. httpx honours HTTPS_PROXY / NO_PROXY (trust_env)."""

    def __init__(self, client: httpx.Client | None = None, max_bytes: int = 64 * 1024 * 1024):
        self.client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(120.0, connect=30.0))
        self.max_bytes = max_bytes
        self._cache: dict[str, bytes] = {}

    def get(self, url: str) -> bytes:
        if url in self._cache:  # one file can serve two datasets (CyberSecEval direct / indirect)
            return self._cache[url]
        if not url.startswith("https://"):
            raise ImportRefused(f"only https downloads are allowed: {url}")
        chunks, size = [], 0
        with self.client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {url}")
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    raise RuntimeError(f"{url} is larger than {self.max_bytes} bytes; refusing")
                chunks.append(chunk)
        data = b"".join(chunks)
        self._cache[url] = data
        return data


def _languages(cases: list[dict]) -> str:
    langs = sorted({(c.get("meta") or {}).get("lang") or "und" for c in cases})
    return ",".join(langs)


def build_public(sec_id: str, dataset_id: str, parts: list[dict], downloader: Downloader,
                 progress: Callable[[str], None] = lambda _m: None) -> tuple[list[dict], dict, dict[str, bytes]]:
    """Download and convert; returns (platform cases, manifest fields, assets)."""
    cases: list[dict] = []
    files_info: list[dict] = []
    sources: list[dict] = []
    assets: dict[str, bytes] = {}
    for d in parts:
        files: dict[str, bytes] = {}
        for f in d["files"]:
            progress(f"download {d['id']}/{f['name']} (~{f['bytes'] // 1024} KB)")
            data = downloader.get(f["url"])
            files[f["name"]] = data
            files_info.append({"dataset": d["id"], "name": f["name"], "url": f["url"], "bytes": len(data), "sha256": sha256_hex(data)})
        internal = adapters.build_internal_cases(d, files)
        converted = [normalise.to_platform(c, d) for c in internal]
        if d["adapter"] in adapters.ASSET_ADAPTERS:
            assets.update(files)
            for c in converted:
                c["meta"]["assets"] = sorted(files)
        if not converted:
            raise RuntimeError(f"{d['id']}: the adapter produced no cases (has the source file changed?)")
        cases += converted
        sources.append({"dataset_id": d["id"], "title": d["title"], "source": d["source"], "license": d["license"],
                        "sensitivity": d["risk"], "risk_note": d["riskNote"], "scorer": d["scorer"], "share": d["share"],
                        "redistribute": d.get("publish", True), "n": len(converted)})
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})[:5]
        raise RuntimeError(f"duplicate case ids across sources: {dupes}")
    single = len(parts) == 1
    manifest = {
        "title": parts[0]["title"] if single else f"secsuite {sec_id} composite ({', '.join(d['id'] for d in parts)})",
        "source": parts[0]["source"] if single else "secsuite manifest: " + ", ".join(d["source"] for d in parts),
        "license": "; ".join(sorted({d["license"] for d in parts})),
        "language": _languages(cases),
        "modality": "text",
        "sensitivity": max((d["risk"] for d in parts), key=normalise.sensitivity_rank),
        "redistribute": all(d.get("publish", True) for d in parts),
        "kind": "public",
        "sources": sources,
        "files": files_info,
        "tool": TOOL,
    }
    return cases, manifest, assets


def import_public(name: str, approved_by: str | None, bucket: DatasetBucket, downloader: Downloader | None = None,
                  note: str | None = None, progress: Callable[[str], None] = lambda _m: None) -> list[dict]:
    alias = check_approver(approved_by)
    targets = resolve(name)
    downloader = downloader or Downloader()
    done = []
    for sec_id, dataset_id, parts in targets:
        cases, manifest, assets = build_public(sec_id, dataset_id, parts, downloader, progress)
        manifest.update(approved_by=alias, approved_at=iso())
        if note:
            manifest["note"] = note
        doc = bucket.write_version(sec_id, dataset_id, cases, manifest, assets)
        bucket.activate(sec_id, dataset_id, doc["version"])
        log.info("dataset imported", extra=kv(sec_id=sec_id, dataset_id=dataset_id, version=doc["version"], n=doc["n"],
                                              sensitivity=doc["sensitivity"], approved_by=alias))
        done.append(doc)
    return done


def import_file(sec_id: str, dataset_id: str, lines: list[str], *, license: str | None, approved_by: str | None,
                bucket: DatasetBucket, language: str | None = None, sensitivity: str = "medium",
                source: str = "internal golden set", title: str | None = None, note: str | None = None) -> dict:
    alias = check_approver(approved_by)
    sec_id = sec_id.upper()
    if not normalise.SEC_ID.match(sec_id):
        raise ImportRefused(f"SEC id must look like SEC-01, got {sec_id!r}")
    if not normalise.DATASET_ID.match(dataset_id):
        raise ImportRefused("dataset_id: 2-63 characters, lower-case letters, digits, '.', '_' or '-'")
    if not license or not license.strip():
        raise ImportRefused("--license is required (for an internal set, e.g. 'internal, confidential')")
    if sensitivity not in normalise.SENSITIVITY:
        raise ImportRefused(f"--sensitivity must be one of {', '.join(normalise.SENSITIVITY)}")
    cases, seen = [], set()
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImportRefused(f"line {lineno}: not valid JSON ({exc.msg})") from exc
        try:
            case = normalise.normalise_golden(sec_id, dataset_id, raw, lineno)
        except normalise.CaseError as exc:
            raise ImportRefused(str(exc)) from exc
        if case["id"] in seen:
            raise ImportRefused(f"line {lineno}: duplicate id {case['id']!r}")
        seen.add(case["id"])
        cases.append(case)
    if not cases:
        raise ImportRefused("the file has no cases")
    manifest = {
        "title": title or dataset_id,
        "source": source,
        "license": license.strip(),
        "language": language or _languages(cases),
        "modality": "text",
        "sensitivity": sensitivity,
        "redistribute": False,
        "kind": "internal",
        "scorers": sorted({c["meta"]["scorer"] for c in cases}),
        "approved_by": alias,
        "approved_at": iso(),
        "tool": TOOL,
    }
    if note:
        manifest["note"] = note
    doc = bucket.write_version(sec_id, dataset_id, cases, manifest)
    bucket.activate(sec_id, dataset_id, doc["version"])
    log.info("golden set imported", extra=kv(sec_id=sec_id, dataset_id=dataset_id, version=doc["version"], n=doc["n"],
                                             sensitivity=sensitivity, approved_by=alias))
    return doc
