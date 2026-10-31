"""A Strands ``Model`` that plays back a script instead of calling an LLM.

It lets the whole stack -- Strands event loop, tool wiring, trust/grounding checks, the
AgentCore HTTP contract, streaming -- be exercised deterministically and without credentials.
It is a test double: it does not do any research by itself.

A script is a list of *steps*, consumed one per model call.  A step is either a list of content
blocks, or a callable ``step(ctx) -> blocks`` that can look at earlier tool results (``ctx``).
Blocks are built with ``say(text)`` and ``call(tool, **input)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterable, Callable, Sequence

from strands.models import Model


def say(text: str) -> dict:
    return {"text": text}


def call(name: str, **tool_input: Any) -> dict:
    return {"toolUse": {"name": name, "input": tool_input}}


@dataclass
class Ctx:
    """What a scripted step can see: the conversation so far."""

    messages: list[dict]
    turn: int = 0

    def _tool_names(self) -> dict[str, str]:
        names = {}
        for m in self.messages:
            for b in m.get("content", []):
                if "toolUse" in b:
                    names[b["toolUse"]["toolUseId"]] = b["toolUse"]["name"]
        return names

    def tool_results(self, name: str | None = None) -> list[Any]:
        """Parsed results (JSON when possible, else text) of earlier calls, oldest first."""
        names = self._tool_names()
        out = []
        for m in self.messages:
            for b in m.get("content", []):
                tr = b.get("toolResult")
                if not tr or (name and names.get(tr["toolUseId"]) != name):
                    continue
                text = "".join(c.get("text", "") for c in tr.get("content", []) if "text" in c)
                try:
                    out.append(json.loads(text))
                except json.JSONDecodeError:
                    out.append(text)
        return out

    def last(self, name: str) -> Any:
        res = self.tool_results(name)
        return res[-1] if res else None


Step = Sequence[dict] | Callable[[Ctx], Sequence[dict]]


class ScriptedModel(Model):
    def __init__(self, steps: Sequence[Step], model_id: str = "scripted"):
        self.steps = list(steps)
        self.config: dict[str, Any] = {"model_id": model_id}
        self.calls: list[dict] = []
        self._turn = 0

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> Any:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # pragma: no cover
        raise NotImplementedError("ScriptedModel does not do structured output")
        yield {}

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncIterable[dict]:
        self.calls.append({
            "turn": self._turn,
            "tools": [s["name"] for s in tool_specs or []],
            "system_prompt": system_prompt,
            "messages": len(messages),
            "first_user_text": _first_text(messages),
        })
        step = self.steps[self._turn] if self._turn < len(self.steps) else [say("(script finished)")]
        blocks = list(step(Ctx(list(messages), self._turn)) if callable(step) else step)
        self._turn += 1

        yield {"messageStart": {"role": "assistant"}}
        uses = 0
        for i, block in enumerate(blocks):
            if "text" in block:
                yield {"contentBlockDelta": {"delta": {"text": block["text"]}, "contentBlockIndex": i}}
                yield {"contentBlockStop": {"contentBlockIndex": i}}
            else:
                tu = block["toolUse"]
                uses += 1
                tool_id = f"tooluse_{self._turn}_{i}"
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_id, "name": tu["name"]}}, "contentBlockIndex": i}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tu.get("input", {}), ensure_ascii=False)}}, "contentBlockIndex": i}}
                yield {"contentBlockStop": {"contentBlockIndex": i}}
        yield {"messageStop": {"stopReason": "tool_use" if uses else "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20}, "metrics": {"latencyMs": 1}}}


def _first_text(messages: list[dict]) -> str:
    for m in messages:
        for b in m.get("content", []):
            if "text" in b:
                return b["text"]
    return ""


# ---------------------------------------------------------------------------------------------
def smoke_script() -> list[Step]:
    """The built-in script for ``MODEL_PROVIDER=scripted`` (``agentcore dev`` contract checks, CI): read the portal, report no change."""
    return [
        [say("Checking the portal snapshot."), call("lookup_portal", file="summary")],
        [call("submit_proposal", proposal={
            "summary": "離線 smoke test：未連線到任何模型或網站，因此沒有提出任何變更。",
            "ops": [],
            "checked": [{"topic": "portal snapshot loaded", "result": "ok"}],
        })],
    ]


def load_script_file(path: str) -> list[Step]:
    """A JSON script: ``[[{"text": ...}, {"toolUse": {"name": ..., "input": {...}}}], ...]`` (ids such as p1/e1 are deterministic)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("script must be a JSON list of steps")
    return data
