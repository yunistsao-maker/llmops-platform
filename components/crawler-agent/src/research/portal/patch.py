"""Apply proposal operations to an in-memory copy of the portal documents.

Operations are RFC-6902-like (``add`` / ``replace`` / ``remove``) with one convenience: array elements
can be addressed by their ``id`` instead of a fragile numeric index::

    {"file": "data/lifecycle.json", "op": "replace", "path": "/events/azure-claude-haiku-4-5-eol/date", "value": "2026-10-15"}
    {"file": "data/lifecycle.json", "op": "add",     "path": "/events/-",                               "value": {...}}

Pointer segments use JSON-pointer escaping (``~1`` = ``/``, ``~0`` = ``~``).

One operation creates a file, and only one kind of file: the deep dive of a model the catalog has, as ``add`` with the
path "" and the whole DeepDive as its value (``create_problem`` says what else must hold)::

    {"file": "data/models/<catalog model id>.json", "op": "add", "path": "", "value": {"modelId": "<catalog model id>", ...}}
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

ALLOWED_FILES = (
    "data/catalog.json", "data/lifecycle.json", "data/litellm.json", "data/requirements.json", "data/open-models.json",
)
EDITABLE_PATTERNS = (re.compile(r"^data/models/[A-Za-z0-9._-]+\.json\Z"),)
#: The one kind of file an operation may create: a model's deep dive, named after the model's catalog id.  (Narrower than
#: the files that may be edited: a name that begins with "." or "-" is no data path the UI asks for - storage._KEY.)
DEEP_DIVE = re.compile(r"data/models/([A-Za-z0-9][A-Za-z0-9._-]{0,99})\.json")  # used with fullmatch
DEEP_DIVES = "data/models/"
CATALOG = "data/catalog.json"
#: array elements can be addressed by the value of one of these keys instead of their position (the first that matches)
ID_KEYS = ("id", "service", "feature", "alias", "name", "scope", "endpoint", "title")
#: The keys among them that say which record an element IS.  ``name`` and ``title`` can address an element, but they are
#: text that gets corrected, and several records may share one (two results of the same benchmark).
RECORD_KEYS = tuple(k for k in ID_KEYS if k not in ("name", "title"))
_INDEX = re.compile(r"-?[0-9]{1,9}")  # ASCII digits only: "²".isdigit() is True and int("²") is an error


class PatchError(ValueError):
    pass


@dataclass
class Op:
    file: str
    op: str
    path: str
    value: Any = None
    evidence: tuple[str, ...] = ()
    rationale: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Op":
        if not isinstance(d, dict):
            raise PatchError("operation must be an object")
        extra = set(d) - {"file", "op", "path", "value", "evidence", "rationale", "confidence"}
        if extra:
            raise PatchError(f"unknown operation keys: {sorted(extra)}")
        for k in ("file", "op", "path"):
            if not isinstance(d.get(k), str):
                raise PatchError(f"operation.{k} must be a string")
        if d["op"] not in ("add", "replace", "remove", "create"):
            raise PatchError(f"operation.op must be add|replace|remove, got {d['op']!r}")
        ev = d.get("evidence", [])
        if not isinstance(ev, list) or not all(isinstance(x, str) for x in ev):
            raise PatchError("operation.evidence must be a list of evidence ids")
        return cls(d["file"], d["op"], d["path"], d.get("value"), tuple(ev), str(d.get("rationale", "")))


def file_is_editable(rel: str) -> bool:
    return rel in ALLOWED_FILES or any(p.match(rel) for p in EDITABLE_PATTERNS)


def creates_file(op: "Op") -> bool:
    """Is ``op`` spelled as the creation of a whole file (``add`` at the path "")?  Whether it may create THAT file is
    ``create_problem``'s answer."""
    return op.op == "add" and op.path == ""


def catalog_ids(docs: dict[str, Any]) -> set[str]:
    """The ids of the models in ``docs``' catalog (whatever shape it has)."""
    catalog = docs.get(CATALOG)
    models = catalog.get("models") if isinstance(catalog, dict) else None
    return {m["id"] for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)} if isinstance(models, list) else set()


def create_problem(docs: dict[str, Any], op: "Op") -> str | None:
    """Why ``op`` (``add`` at the path "") may not create ``op.file`` in ``docs`` - None when it may.

    A file is created only when it is a deep dive that does not exist yet, of a model that ``docs``' catalog has (the
    catalog as it stands at this operation: an earlier operation of the same proposal may have added the model) and
    that no other deep dive is about already, and that says it is that model's (``modelId``).  Everything else -
    another file, a file that is there, a deep dive of no catalog model, a second one of a model - is refused: a new
    record goes into its list, a digest into `digest`, a source into `newSources`.
    """
    found = DEEP_DIVE.fullmatch(op.file)
    if found is None:
        if op.file in docs:
            return "refusing to replace a whole file; address a field or array element"
        return (f"an operation creates no file but a model's deep dive (data/models/<catalog model id>.json), and {op.file} is not one: a new "
                "record is an `add` on its list (e.g. /models/-), a new digest goes in `digest`, a new source in `newSources`")
    if op.file in docs:
        return (f"{op.file} already exists: a deep dive is created once. Change the fields that differ instead "
                "(e.g. `add /whatsNew/-`, `replace /tldr`)")
    model_id = found[1]
    if model_id not in catalog_ids(docs):
        return (f"{model_id!r} is not the id of a model in {CATALOG} (as it stands at this operation): a deep dive belongs to a catalog "
                "model and is named after its id. Add the model first (an earlier operation of this proposal, `add` on /models/-), "
                "or name the file after the model's catalog id")
    # (a deep dive under another name that says it is about this model: a second one would leave the portal two)
    other = next((rel for rel, doc in sorted(docs.items())
                  if rel.startswith(DEEP_DIVES) and isinstance(doc, dict) and doc.get("modelId") == model_id), None)
    if other is not None:
        return (f"{other} is a deep dive of {model_id!r} already (its modelId): a model has one deep dive. Change that one field by "
                "field instead; a deep dive whose file name is not its model's id is for a person to rename")
    if not isinstance(op.value, dict):
        return "a deep dive is one object of the type DeepDive (describe_schema shows it)"
    if op.value.get("modelId") != model_id:
        shown = op.value.get("modelId")
        return (f"the deep dive's modelId is {str(shown)[:80]!r}{'' if isinstance(shown, str) else ' (not text)'} and its file is "
                f"{op.file}: a deep dive is named after the model it is about, so modelId must be {model_id!r}")
    return None


def _unescape(seg: str) -> str:
    return seg.replace("~1", "/").replace("~0", "~")


def split_path(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError(f"path must start with '/': {path!r}")
    return [_unescape(s) for s in path[1:].split("/")]


def is_index(seg: str) -> bool:
    """Is this path segment an array position (rather than an element's id)?"""
    return _INDEX.fullmatch(seg) is not None


def _find_in_list(lst: list, seg: str) -> int:
    if is_index(seg):
        i = int(seg)
        if not -len(lst) <= i < len(lst):
            raise PatchError(f"index {i} out of range (len {len(lst)})")
        return i % len(lst) if i < 0 else i
    for key in ID_KEYS:
        hits = [i for i, el in enumerate(lst) if isinstance(el, dict) and el.get(key) == seg]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise PatchError(f"ambiguous: {len(hits)} elements have {key}={seg!r}; use a numeric index")
    raise PatchError(f"no array element with id/service/feature/alias/name/scope/endpoint/title == {seg!r}")


def _walk(root: Any, segs: list[str]) -> Any:
    node = root
    for seg in segs:
        if isinstance(node, dict):
            if seg not in node:
                raise PatchError(f"no such key {seg!r}")
            node = node[seg]
        elif isinstance(node, list):
            node = node[_find_in_list(node, seg)]
        else:
            raise PatchError(f"cannot descend into {type(node).__name__} at {seg!r}")
    return node


def get_at(docs: dict[str, Any], file: str, path: str) -> Any:
    """Value at ``path`` in ``docs[file]`` (raises PatchError if it does not exist)."""
    if file not in docs:
        raise PatchError(f"unknown file {file}")
    return _walk(docs[file], split_path(path))


def key_of(element: Any) -> str | None:
    """The segment an array element can be addressed by instead of its position (None when it has no id of that kind)."""
    if not isinstance(element, dict):
        return None
    for key in ID_KEYS:
        value = element.get(key)
        if isinstance(value, str) and value and not is_index(value) and value != "-":
            return value
    return None


def record_key(element: Any) -> tuple[str, str] | None:
    """(key, value) that identifies a record among the records of its list: what pages look it up by, and what an
    operation addresses it by.  A software release (an object with ``version`` and ``channel`` / ``supported``) is
    identified by its version.  None for an element that is not a record of that kind."""
    if not isinstance(element, dict):
        return None
    for key in RECORD_KEYS:
        value = element.get(key)
        if isinstance(value, str) and value:
            return key, value
    if isinstance(element.get("version"), str) and element["version"] and ("channel" in element or "supported" in element):
        return "version", element["version"]
    return None


def positions_on_the_way(root: Any, segs: list[str]) -> list[tuple[int, str | None]]:
    """(index of the segment, the id it could be written as or None) for every array *position* that ``segs`` passes
    through before its last segment.

    The last segment may be a position: what it addresses is compared as a whole with the value an operation records
    (``before``).  A position on the way is tied to nothing - once the array changes, the same path means the same field
    of another element.
    """
    out: list[tuple[int, str | None]] = []
    node = root
    for k, seg in enumerate(segs[:-1]):
        if isinstance(node, list):
            try:
                at = _find_in_list(node, seg)
            except PatchError:
                break
            if is_index(seg):
                ident = key_of(node[at])
                unique = ident is not None and sum(1 for el in node if key_of(el) == ident) == 1
                out.append((k, ident if unique else None))
            node = node[at]
        elif isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            break
    return out


def chain_at(docs: dict[str, Any], file: str, path: str) -> list[Any]:
    """Nodes from the file root down to ``path`` (inclusive).  A trailing ``-`` means the last array element."""
    if file not in docs:
        raise PatchError(f"unknown file {file}")
    node = docs[file]
    out = [node]
    for seg in split_path(path):
        if isinstance(node, dict):
            if seg not in node:
                raise PatchError(f"no such key {seg!r}")
            node = node[seg]
        elif isinstance(node, list):
            if seg == "-":
                if not node:
                    raise PatchError("empty array")
                node = node[-1]
            else:
                node = node[_find_in_list(node, seg)]
        else:
            raise PatchError(f"cannot descend into {type(node).__name__} at {seg!r}")
        out.append(node)
    return out


def apply_op(docs: dict[str, Any], op: Op) -> str:
    """Apply one operation in place.  Returns a short human-readable description."""
    if op.op not in ("add", "replace", "remove"):
        # "create" is understood by Op.from_dict only so that it can be turned down with a reason
        raise PatchError(f"operation {op.op!r} is not available: a new digest goes in `digest`, a new record is an `add` (e.g. on /models/-), "
                         "a new deep dive is an `add` with the path \"\" on data/models/<catalog model id>.json")
    if creates_file(op) and (op.file not in docs or DEEP_DIVE.fullmatch(op.file)):
        problem = create_problem(docs, op)
        if problem:
            raise PatchError(problem)
        docs[op.file] = copy.deepcopy(op.value)
        return f"create {op.file}"
    if op.file not in docs:
        raise PatchError(f"unknown file {op.file}")
    if not file_is_editable(op.file):
        raise PatchError(f"{op.file} cannot be edited by operations (sources go in newSources, digests in digest)")
    segs = split_path(op.path)
    if not segs:
        raise PatchError("refusing to replace a whole file; address a field or array element")
    root = docs[op.file]
    parent = _walk(root, segs[:-1])
    last = segs[-1]
    if op.op == "add":
        value = copy.deepcopy(op.value)
        if isinstance(parent, list):
            # A second record with the key of one that is there does not "add" anything: pages look a record up by that
            # key, so it would stand in for the existing one - and afterwards the key would address neither.
            ident = record_key(value)
            if ident is not None and any(record_key(el) == ident for el in parent):
                if set(value) <= {"id", "note"}:  # a reference to a source
                    raise PatchError(f"this list already cites {ident[1]!r}; a page is cited once (drop this operation - what is "
                                     "there stays as it is)")
                raise PatchError(f"this list already has a record with {ident[0]} = {ident[1]!r}; change that record "
                                 f"({'/'.join(op.path.split('/')[:-1])}/{ident[1]}/<field>) instead of adding a second one")
            if last == "-":
                parent.append(value)
            elif is_index(last) and not last.startswith("-"):
                idx = int(last)
                if idx > len(parent):
                    raise PatchError(f"index {idx} out of range for insert (len {len(parent)})")
                parent.insert(idx, value)
            else:
                raise PatchError("to append to an array use '/-' (or a numeric index)")
        elif isinstance(parent, dict):
            if last in parent:
                raise PatchError(f"{last!r} already exists at {op.path}; use replace")
            parent[last] = value
        else:
            raise PatchError(f"cannot add into {type(parent).__name__}")
        return f"add {op.file}{op.path}"
    if op.op == "replace":
        value = copy.deepcopy(op.value)
        if isinstance(parent, list):
            parent[_find_in_list(parent, last)] = value
        elif isinstance(parent, dict):
            if last not in parent:
                raise PatchError(f"{last!r} does not exist at {op.path}; use add")
            parent[last] = value
        else:
            raise PatchError(f"cannot replace inside {type(parent).__name__}")
        return f"replace {op.file}{op.path}"
    # remove
    if isinstance(parent, list):
        del parent[_find_in_list(parent, last)]
    elif isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"{last!r} does not exist at {op.path}")
        del parent[last]
    else:
        raise PatchError(f"cannot remove from {type(parent).__name__}")
    return f"remove {op.file}{op.path}"
