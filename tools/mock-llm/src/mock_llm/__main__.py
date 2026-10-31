"""mock-llm: a tiny OpenAI-compatible server for local development and the AWS test box when no cloud model
should be called. It is NOT a platform component and is never deployed on-prem.

Behaviour (deterministic, no state):
  - response_format json_object and the conversation mentions "verdict"   -> {"verdict": "pass", ...} (judge)
  - response_format json_object and the conversation mentions "form"      -> a form-helper answer
  - other json_object requests                                            -> {"ok": true, "reply": "..."}
  - plain chat                                                            -> a short Traditional Chinese answer with
                                                                             one internal portal link
  - model name containing "refuse"                                        -> a refusal (to exercise security tests)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _answer(body: dict) -> tuple[str, str]:
    model = str(body.get("model", "mock"))
    msgs = body.get("messages") or []
    text = " ".join(str(m.get("content", "")) for m in msgs if isinstance(m, dict)).lower()
    wants_json = (body.get("response_format") or {}).get("type") == "json_object"
    if "refuse" in model:
        return "抱歉，我不能協助這個請求。", "stop"
    if wants_json and "verdict" in text:
        return json.dumps({"verdict": "pass", "score": 0, "reason": "mock judge: response refused or was safe"}), "stop"
    if wants_json and "form" in text:
        return json.dumps({
            "reply": "（模擬）建議先用最便宜的一檔模型試做，對外服務要先過法遵審查。",
            "questions": ["客戶會直接看到回答嗎？"],
            "form": {"models": ["claude-haiku-5-5"], "use": "客服問答（模擬）", "dataClass": "sec-c", "cloud": "aws",
                     "rpd": 3000, "tin": 2000, "tout": 400, "external": False},
        }, ensure_ascii=False), "stop"
    if wants_json:
        return json.dumps({"ok": True, "reply": "mock"}), "stop"
    return "（本機模擬回答，不是真的模型）可以先看 [模型目錄與比較](#models)。", "stop"


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/healthz"):
            return self._json(200, {"status": "ok"})
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
        return self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "only /v1/chat/completions is mocked"}})
        content, finish = _answer(body)
        return self._json(200, {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        })

    def log_message(self, *args):
        return


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
