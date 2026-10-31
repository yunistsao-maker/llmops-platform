#!/usr/bin/env python3
"""Write the OpenAPI documents of the HTTP services to contracts/openapi/<service>.json (the portal's API contract).

The apps are only constructed, never started: nothing connects to a database, object storage or the gateway.
Usage: python3 tools/export_openapi.py [--out contracts/openapi] [--check]
Needs each service's dependencies (fastapi etc.); run it in the dev venv or in CI after installing the locks.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {  # service -> (package, factory module)
    "content-api": ("content_api", "content_api.app"),
    "assistant-api": ("assistant_api", "assistant_api.app"),
    "request-api": ("request_api", "request_api.app"),
    "test-orchestrator": ("test_orchestrator", "test_orchestrator.api"),
}
DUMMY_ENV = {  # unreachable placeholders: constructing an app must not need real services
    "AUTH_MODE": "dev", "DATABASE_URL": "postgresql://openapi:openapi@127.0.0.1:9/openapi",
    "S3_ENDPOINT_URL": "http://127.0.0.1:9", "S3_ACCESS_KEY_ID": "x", "S3_SECRET_ACCESS_KEY": "x",
    "LLM_GATEWAY_URL": "http://127.0.0.1:9", "LLM_GATEWAY_KEY": "x", "ORCHESTRATOR_BACKGROUND": "false",
    "REQUEST_API_WORKER": "false", "ROLE_ADMIN": "llmops-admins",
}


def export(service: str) -> dict:
    pkg, module = SERVICES[service]
    sys.path[:0] = [str(ROOT / "libs/llmops-common/src"), str(ROOT / "components" / service / "src")]
    mod = importlib.import_module(module)
    app = mod.create_app()
    doc = app.openapi()
    doc.setdefault("info", {})["x-llmops-service"] = service
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "contracts/openapi")
    ap.add_argument("--check", action="store_true", help="fail if a document differs from what is in --out")
    a = ap.parse_args()
    for k, v in DUMMY_ENV.items():
        os.environ.setdefault(k, v)
    a.out.mkdir(parents=True, exist_ok=True)
    stale = []
    for service in SERVICES:
        text = json.dumps(export(service), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        path = a.out / f"{service}.json"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            stale.append(path.name)
            if not a.check:
                path.write_text(text, encoding="utf-8")
    if a.check and stale:
        print("OpenAPI out of date (run python3 tools/export_openapi.py):", ", ".join(stale))
        return 1
    print(f"{len(SERVICES)} documents, {len(stale)} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
