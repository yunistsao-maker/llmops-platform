"""Request body validation against contracts/schemas/request.schema.json (copied into the image)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .domain import DEFAULTS, FIELDS

_TRIMMED = ("team", "alias", "use_case", "data_class_other", "note")


class ValidationFailed(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["msg"] for e in errors))
        self.errors = errors


class RequestValidator:
    def __init__(self, schema: dict):
        Draft202012Validator.check_schema(schema)
        self.schema = schema
        self.properties = frozenset(schema.get("properties", {}))
        self._validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    @classmethod
    def load(cls, path: Path) -> "RequestValidator":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def errors(self, doc) -> list[dict]:
        out = []
        for err in sorted(self._validator.iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path]):
            msg = err.message if len(err.message) <= 300 else err.message[:297] + "..."
            out.append({"loc": ["body", *err.absolute_path], "msg": msg, "type": str(err.validator)})
        return out

    def normalize(self, body) -> dict:
        """Validate a full request document; return every field with schema defaults applied.

        Leading/trailing blanks are trimmed first, so "   " fails minLength the way the portal form does.
        `target_date` becomes a date; `data_class_other` is kept only for data class `other`.
        """
        if not isinstance(body, dict):
            raise ValidationFailed([{"loc": ["body"], "msg": "request body must be a JSON object", "type": "type"}])
        doc = {k: (v.strip() if k in _TRIMMED and isinstance(v, str) else v) for k, v in body.items()}
        errors = self.errors(doc)
        if errors:
            raise ValidationFailed(errors)
        out = {name: None for name in FIELDS}
        out.update({k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()})
        out.update(doc)
        out["models"] = list(out.get("models") or [])
        if out["data_class"] != "other":
            out["data_class_other"] = None
        for name in ("alias", "note"):
            if out.get(name) == "":
                out[name] = None
        if out.get("target_date"):
            out["target_date"] = dt.date.fromisoformat(out["target_date"])
        return out


def document_from_row(row: dict) -> dict:
    """The stored request as a schema document (input for a resubmission merge)."""
    doc = {}
    for name in FIELDS:
        value = row.get(name)
        if value is None:
            continue
        if isinstance(value, dt.date):
            value = value.isoformat()
        elif isinstance(value, (list, tuple)):
            value = list(value)
        doc[name] = value
    return doc
