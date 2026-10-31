"""dataset-manager: approve and import security test datasets into the datasets bucket.

    python -m dataset_manager list [--json]
    python -m dataset_manager show <dataset | SEC-xx> [--json]
    python -m dataset_manager import <dataset | SEC-xx | all> --approved-by <alias> [--note TEXT]
    python -m dataset_manager import-file <SEC-xx> <dataset_id> <file.jsonl | -> --license TEXT --approved-by <alias>
                                          [--sensitivity medium] [--language zh-TW] [--source TEXT] [--title TEXT]

`import` downloads a public dataset of the secsuite manifest (through HTTPS_PROXY); `SEC-xx` imports every public
dataset of that item as one composite. `import-file` takes an internal JSONL golden set
({id, prompt | turns, expected?, meta?} per line). Each import writes a new version and points index.json at it.
Exit codes: 0 ok, 1 failure, 2 refused (missing approver, bad input).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from llmops_common.log import get_logger, setup_logging
from llmops_common.storage import S3Store, bucket

from . import manager
from .layout import DatasetBucket

log = get_logger("dataset_manager")
RISK = {"none": "none", "low": "low", "medium": "medium", "medium-high": "med-high", "high": "HIGH", "highest": "HIGHEST"}


def _bucket(store=None) -> DatasetBucket:
    return DatasetBucket(store or S3Store.from_env(), bucket("datasets"))


def cmd_list(args, store=None) -> int:
    manifest = manager.load_manifest()
    try:
        dsb = _bucket(store)
        index = dsb.index()
    except Exception as exc:  # the catalogue is still useful without the bucket
        log.warning("datasets bucket not reachable", extra={"fields": {"error": type(exc).__name__}})
        index = {}
    rows = []
    for d in manifest["datasets"]:
        active = index.get(d["item"], {})
        rows.append({"id": d["id"], "sec_id": d["item"], "title": d["title"], "license": d["license"], "sensitivity": d["risk"],
                     "bytes": sum(f["bytes"] for f in d["files"]), "download": bool(d["files"]),
                     "active": active if active.get("dataset_id") in (d["id"], manager.composite_id(d["item"])) else None})
    if args.json:
        print(json.dumps({"datasets": rows, "index": index}, ensure_ascii=False, indent=1))
        return 0
    print(f"{'SEC':7} {'dataset':20} {'size':>8}  {'risk':8} {'license':28} active")
    for r in rows:
        size = f"{r['bytes'] / 1024:.0f} KB" if r["download"] else "built-in"
        act = f"{r['active']['dataset_id']}@v{r['active']['version']}" if r["active"] else "-"
        print(f"{r['sec_id']:7} {r['id']:20} {size:>8}  {RISK.get(r['sensitivity'], r['sensitivity']):8} {r['license'][:28]:28} {act}")
    print("\nindex.json (what test-orchestrator plans with):")
    for sec, entry in sorted(index.items()):
        print(f"  {sec}: {entry['dataset_id']}@v{entry['version']}")
    if not index:
        print("  (empty: nothing approved yet)")
    print("\nImport one dataset:  python -m dataset_manager import jbb-harmful --approved-by <alias>")
    print("Import a SEC item:   python -m dataset_manager import SEC-01 --approved-by <alias>   (all its public sources)")
    return 0


def cmd_show(args, store=None) -> int:
    dsb = _bucket(store)
    try:
        targets = manager.resolve(args.dataset)
    except manager.ImportRefused:  # not a public dataset: an internal golden set already in the bucket?
        found = sorted({(m["sec_id"], m["dataset_id"]) for m in dsb.manifests() if m.get("dataset_id") == args.dataset})
        if not found:
            raise
        targets = [(sec, ds, []) for sec, ds in found]
    index = dsb.index()
    out = []
    for sec_id, dataset_id, parts in targets:
        out.append({"sec_id": sec_id, "dataset_id": dataset_id, "active": index.get(sec_id),
                    "sources": [{k: v for k, v in d.items()} for d in parts],
                    "versions": [{k: m.get(k) for k in ("version", "n", "sha256", "license", "language", "sensitivity",
                                                         "approved_by", "approved_at")}
                                 for m in dsb.manifests(sec_id, dataset_id)]})
    print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=1))
    return 0


def cmd_import(args, store=None, downloader=None) -> int:
    dsb = _bucket(store)
    docs = manager.import_public(args.dataset, args.approved_by, dsb, downloader=downloader, note=args.note,
                                 progress=lambda m: print(m, file=sys.stderr))
    for d in docs:
        print(f"{d['sec_id']} {d['dataset_id']} v{d['version']}: {d['n']} cases, sha256 {d['sha256'][:12]}…, "
              f"sensitivity {d['sensitivity']}, approved by {d['approved_by']} (now active in index.json)")
    return 0


def cmd_import_file(args, store=None) -> int:
    if args.file == "-":
        lines = sys.stdin.read().splitlines()
    else:
        with open(args.file, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    doc = manager.import_file(args.sec_id, args.dataset_id, lines, license=args.license, approved_by=args.approved_by,
                              bucket=_bucket(store), language=args.language, sensitivity=args.sensitivity, source=args.source,
                              title=args.title, note=args.note)
    print(f"{doc['sec_id']} {doc['dataset_id']} v{doc['version']}: {doc['n']} cases (scorers: {', '.join(doc['scorers'])}), "
          f"sha256 {doc['sha256'][:12]}…, approved by {doc['approved_by']} (now active in index.json)")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m dataset_manager", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="public datasets, their licence and risk, and what is active")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show", help="one dataset or SEC item with its imported versions")
    p.add_argument("dataset")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("import", help="download, convert and approve a public dataset (dataset id, SEC-xx or all)")
    p.add_argument("dataset")
    p.add_argument("--approved-by", help="alias of the person approving the import (required)")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("import-file", help="import an internal JSONL golden set")
    p.add_argument("sec_id")
    p.add_argument("dataset_id")
    p.add_argument("file", help="path to the .jsonl file, or - for stdin")
    p.add_argument("--license", help="licence or usage terms (required)")
    p.add_argument("--approved-by", help="alias of the person approving the import (required)")
    p.add_argument("--sensitivity", default="medium", help="none | low | medium | medium-high | high | highest")
    p.add_argument("--language", help="default: the languages found in meta.lang")
    p.add_argument("--source", default="internal golden set")
    p.add_argument("--title")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_import_file)
    return ap


def main(argv: list[str] | None = None, store=None, downloader=None) -> int:
    setup_logging(os.environ.get("COMPONENT", "dataset-manager"))
    args = parser().parse_args(argv)
    try:
        if args.fn is cmd_import:
            return cmd_import(args, store, downloader)
        return args.fn(args, store)
    except manager.ImportRefused as exc:
        print(str(exc), file=sys.stderr)
        log.error("import refused", extra={"fields": {"cmd": args.cmd, "reason": str(exc)[:300]}})
        return 2
    except Exception as exc:  # noqa: BLE001 - a job reports failure through its exit code
        log.exception("dataset-manager failed", extra={"fields": {"cmd": args.cmd}})
        print(f"failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
