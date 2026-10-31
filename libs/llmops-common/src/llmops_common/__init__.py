"""Shared building blocks for every LLMOps platform component.

Modules: config, log, storage (S3 API), db (PostgreSQL), queue (events + jobs), health, auth (OIDC),
fastapi_support (services only), llm (gateway client), gates, ids.
See docs/runtime-contract.md for the rules these helpers implement.
"""
__version__ = "0.1.0"
