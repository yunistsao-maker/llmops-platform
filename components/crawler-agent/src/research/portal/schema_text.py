"""Render a JSON-Schema definition as compact TypeScript-like text for the model.

Raw JSON Schema is verbose; this keeps the field names, enums, optionality and the schema's own
descriptions (which carry the portal's modelling rules) in about a fifth of the tokens.
"""

from __future__ import annotations

from typing import Any

_PRIMITIVE_ALIASES = {"ISODate": "ISODate(YYYY-MM-DD)", "ISODateTime": "ISODateTime(UTC, ...Z)"}


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def render_definition(schema: dict, name: str, max_types: int = 12) -> str:
    defs: dict[str, Any] = schema["definitions"]
    if name not in defs:
        close = sorted(n for n in defs if name.lower() in n.lower())[:8]
        raise KeyError(f"unknown definition {name!r}" + (f"; similar: {close}" if close else f"; known: {sorted(defs)[:40]}"))
    queue: list[str] = [name]
    done: list[str] = []
    blocks: list[str] = []

    def type_expr(node: dict, indent: int) -> str:
        if "$ref" in node:
            ref = _ref_name(node["$ref"])
            d = defs.get(ref, {})
            if ref in _PRIMITIVE_ALIASES:
                return _PRIMITIVE_ALIASES[ref]
            if "enum" in d:
                return "|".join(f'"{v}"' for v in d["enum"])
            if d.get("type") in ("string", "number", "boolean") and "properties" not in d:
                return d["type"]
            if ref not in queue and ref not in done:
                queue.append(ref)
            return ref
        if "anyOf" in node:
            return "|".join(type_expr(n, indent) for n in node["anyOf"])
        if "enum" in node:
            return "|".join(f'"{v}"' for v in node["enum"])
        if "const" in node:
            return f'"{node["const"]}"'
        t = node.get("type")
        if isinstance(t, list):
            return "|".join(t)
        if t == "array":
            inner = type_expr(node.get("items", {}), indent)
            return f"({inner})[]" if "|" in inner else f"{inner}[]"
        if t == "object" or "properties" in node:
            if "properties" not in node:
                return "object"
            return "{\n" + object_lines(node, indent + 1) + "  " * indent + "}"
        return t or "any"

    def object_lines(node: dict, indent: int) -> str:
        req = set(node.get("required", []))
        lines = []
        for key, sub in node.get("properties", {}).items():
            desc = sub.get("description")
            if not desc and "$ref" in sub:
                desc = defs.get(_ref_name(sub["$ref"]), {}).get("description")
            comment = f"  // {desc}" if desc else ""
            lines.append("  " * indent + f"{key}{'' if key in req else '?'}: {type_expr(sub, indent)}{comment}\n")
        return "".join(lines)

    while queue and len(done) < max_types:
        cur = queue.pop(0)
        if cur in done:
            continue
        done.append(cur)
        node = defs[cur]
        head = f"// {node['description']}\n" if node.get("description") else ""
        if "enum" in node:
            blocks.append(f"{head}{cur} = " + "|".join(f'"{v}"' for v in node["enum"]))
        elif node.get("type") == "object" or "properties" in node:
            strict = "  // no other keys allowed\n" if node.get("additionalProperties") is False else ""
            blocks.append(f"{head}{cur} {{\n{object_lines(node, 1)}{strict}}}")
        else:
            blocks.append(f"{head}{cur} = {type_expr(node, 0)}")
    if queue:
        blocks.append("// (more referenced types omitted: " + ", ".join(q for q in queue if q not in done) + ")")
    return "\n\n".join(blocks)
