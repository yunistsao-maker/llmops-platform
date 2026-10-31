"""Builders for the platform side of the tests: a published release in the curated bucket, and a mock LLM gateway."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from research.portal.data import BUNDLED_DIR

RELEASE = "20261001T000000Z"


def publish_snapshot(store, bucket: str = "llmops-curated", release: str = RELEASE) -> str:
    """The agent's bundled snapshot as the live release, in the publisher's layout (portal/releases/<id>/ + the
    {"current": id, "history": [...]} pointer)."""
    for path in sorted(BUNDLED_DIR.rglob("*.json")):
        rel = path.relative_to(BUNDLED_DIR).as_posix()
        if rel != "snapshot_manifest.json":
            store.put_bytes(bucket, f"portal/releases/{release}/{rel}", path.read_bytes(), "application/json")
    store.put_json(bucket, f"portal/releases/{release}/release.json",
                   {"release_id": release, "created_at": "2026-10-01T00:00:00Z", "source_proposals": [], "held": False})
    store.put_json(bucket, "portal/manifest.json", {
        "schema": "portal-manifest/v2", "current": release, "published_at": "2026-10-01T00:00:00Z",
        "history": [{"release_id": release, "published_at": "2026-10-01T00:00:00Z", "kind": "bootstrap", "summary": "first", "changed": 9}]})
    return release


# ---------------------------------------------------------------------------------------------- a mock gateway
def sse(chunks: list[dict]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def _chunk(delta: dict, finish: str | None = None, model: str = "agent-research") -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def tool_call_turn(name: str, arguments: dict, call_id: str = "call_1") -> bytes:
    """One streamed assistant turn that calls ``name`` (OpenAI chat-completions streaming format)."""
    args = json.dumps(arguments, ensure_ascii=False)
    return sse([
        _chunk({"role": "assistant", "content": None,
                "tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": ""}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[: len(args) // 2]}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[len(args) // 2:]}}]}),
        _chunk({}, finish="tool_calls"),
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": "agent-research", "choices": [],
         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
    ])


def text_turn(text: str) -> bytes:
    return sse([_chunk({"role": "assistant", "content": text}), _chunk({}, finish="stop")])


class MockGateway:
    """An OpenAI-compatible gateway on httpx.MockTransport: plays ``turns`` in order and records every request."""

    def __init__(self, turns: list[bytes | Callable[[dict], Any]]):
        self.turns = list(turns)
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content or b"{}")
        self.bodies.append(body)
        if not self.turns:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text_turn("(script finished)"))
        turn = self.turns.pop(0)
        if callable(turn):
            return turn(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=turn)

    def client(self, base_url: str = "http://llm-gateway:4000/v1", api_key: str = "sk-test-virtual-key"):
        import openai

        return openai.AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))
