"""view-builder entry point.

    python -m view_builder worker     consume queue `view-builder` (default; health on HEALTH_PORT)
    python -m view_builder build      one-shot: compile the view from the curated bucket and move the pointer
    python -m view_builder seed --data-dir DIR [--security-run FILE] [--matrix FILE] [--editorial FILE]
                                      [--release-id ID] [--source LABEL] [--force] [--build] [--ensure-bucket]
                                      upload a starter release + manifest (one release), editorial, matrix, security
    python -m view_builder compile --data-dir DIR [--security-run F] [--matrix F] [--editorial F] [--today D] --out F
                                      offline: compile from local files, no object storage
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from llmops_common.config import ConfigError, env
from llmops_common.log import get_logger, kv, setup_logging

log = get_logger("view_builder")

HERE = Path(__file__).resolve()


def _default_file(name: str, repo_rel: str) -> Path | None:
    """Bundled seed file: $SEED_DIR/<name> in the image, else the repo copy when run from a checkout."""
    for cand in (Path(env("SEED_DIR", "/app/seed")) / name, HERE.parents[4] / repo_rel if len(HERE.parents) > 4 else None):
        if cand is not None and cand.is_file():
            return cand
    return None


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value}") from exc


def _store_and_bucket():
    from llmops_common.storage import S3Store, bucket

    return S3Store.from_env(), bucket("curated")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m view_builder", description="Compile the LLMOps portal view model.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("worker", help="consume queue view-builder (default)")
    b = sub.add_parser("build", help="build once from the curated bucket")
    b.add_argument("--today", type=_date, help="portal-local date for date-relative fields (default: now in PORTAL_TZ)")

    def inputs(sp, data_required=True):
        sp.add_argument("--data-dir", type=Path, required=data_required, help="llmops-portal style data/ directory")
        sp.add_argument("--security-run", type=Path, help="security results: legacy run file or portal shape")
        sp.add_argument("--matrix", type=Path, help="SEC-01..32 matrix (contracts/fixtures/security-matrix.json shape)")
        sp.add_argument("--editorial", type=Path, help="editorial overlay (editorial/current.json shape)")
        sp.add_argument("--source", help="provenance label for the release (default: git remote@sha of the data dir)")

    s = sub.add_parser("seed", help="upload a starter release, manifest, editorial, matrix and security results")
    inputs(s, data_required=False)
    s.add_argument("--no-release", action="store_true",
                   help="only editorial, matrix and security results: the release comes from `publisher bootstrap`")
    s.add_argument("--release-id", help="release id (default: now, e.g. 20261031T061346Z)")
    s.add_argument("--force", action="store_true", help="replace an existing portal/manifest.json")
    s.add_argument("--build", action="store_true", help="also build the view right away")
    s.add_argument("--ensure-bucket", action="store_true", help="create BUCKET_CURATED if missing (local / test only)")
    c = sub.add_parser("compile", help="offline compile from local files (no object storage)")
    inputs(c)
    c.add_argument("--today", type=_date, help="date for date-relative fields (default: now in PORTAL_TZ)")
    c.add_argument("--out", type=Path, required=True, help="write the view JSON here")
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("COMPONENT", "view-builder"))
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    cmd = args.cmd or "worker"
    try:
        if cmd == "worker":
            from .worker import run_worker

            store, bkt = _store_and_bucket()
            run_worker(store, bkt)
            return 0
        if cmd == "build":
            from .worker import Builder

            store, bkt = _store_and_bucket()
            factory = None
            if env("DATABASE_URL", None):
                from llmops_common import db

                factory = db.connection
            builder = Builder.from_env(store, bkt, connection_factory=factory)
            if args.today:
                builder.today = lambda: args.today  # type: ignore[method-assign]
            result = builder.build("cli")
            print(json.dumps(result.__dict__ if result else {"view_id": None, "note": "no release yet"}, ensure_ascii=False))
            return 0 if result else 3
        if cmd == "seed":
            from .store_io import build_and_publish, seed
            from .worker import portal_tz

            editorial = args.editorial or _default_file("editorial.json", "components/view-builder/editorial/current.json")
            matrix = args.matrix or _default_file("security-matrix.json", "contracts/fixtures/security-matrix.json")
            store, bkt = _store_and_bucket()
            if args.ensure_bucket:
                store.ensure_bucket(bkt)
            if not args.no_release and args.data_dir is None:
                raise ValueError("seed needs --data-dir (or --no-release when the publisher made the release)")
            done = seed(store, bkt, args.data_dir, security_run=args.security_run, matrix=matrix, editorial=editorial,
                        release_id=args.release_id, source=args.source, force=args.force,
                        release=not args.no_release)
            if args.build:
                res = build_and_publish(store, bkt, today=dt.datetime.now(portal_tz()).date())
                done["view_id"] = res.view_id
            print(json.dumps(done, ensure_ascii=False))
            return 0
        if cmd == "compile":
            from .compile import build_view
            from .store_io import encode_view, local_inputs
            from .worker import portal_tz

            inp = local_inputs(args.data_dir, security_run=args.security_run, matrix=args.matrix,
                               editorial=args.editorial, source=args.source)
            view = build_view(inp, args.today or dt.datetime.now(portal_tz()).date())
            args.out.write_bytes(encode_view(view))
            for w in inp.warnings:
                log.warning("build note", extra=kv(note=w))
            print(json.dumps({"out": str(args.out), "models": len(view["models"]), "news": len(view["news"]),
                              "ret": len(view["ret"]), "warnings": len(inp.warnings)}, ensure_ascii=False))
            return 0
    except (ConfigError, ValueError, FileExistsError, FileNotFoundError) as exc:
        log.error("failed", extra=kv(cmd=cmd, error=f"{type(exc).__name__}: {exc}"))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
