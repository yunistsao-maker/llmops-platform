"""What really differs between two versions of the portal documents.

A proposal says what it intends ("replace this field").  What it *does* can be much more: one ``replace`` with a
smaller value deletes whatever was nested there, and one ``replace`` of a list rewrites every record in it.  The gates
of the publishing pipeline and the text a reviewer reads are therefore derived from this comparison, never from how an
operation was spelled.

    modified   a value that existed and has another value now
    removed    an object key or a list element that existed and is gone (with everything below it)
    added      an object key or a list element that did not exist (with everything below it)
    reordered  the records of a list stand in another order (before / after: their ids in the old and the new order),
               or something new was put IN FRONT of what a list held (after: the new order, the new entries among them)

Lists of records are matched by their id (the same keys the patch operations address them by), so a missing record is a
removal wherever it was and a moved record is not "one removed, one added" - but the new order is a change of its own:
pages show the first price sheet, the first offering.  For the same reason a new record that is put in front of
existing ones is reported as an addition AND as a new order: only what is added at the end leaves what was there where
it was.  Other lists are aligned by content, so an insertion in the middle is one addition (and, again, a new order).
A list of source references (``sources: [{"id": ...}]``) is one value: citing other pages for
a fact is a modification of the fact's citations, not a deletion of data.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field as _field
from typing import Any, Iterable

from .patch import RECORD_KEYS  # (``name`` and ``title`` are not among them: matching on text that gets corrected would turn a fixed typo into "record removed, record added")

MODIFIED, REMOVED, ADDED, REORDERED = "modified", "removed", "added", "reordered"
#: how a new entry is shown in the ``after`` of a reordering of a list without ids
NEW_ENTRY = "+"
#: Aligning two lists by content costs up to (length x length) steps when many elements are alike.  What two lists share
#: at their start and their end is set aside first (an append costs nothing); what is left in the middle is aligned only
#: while that product stays small, and compared position by position beyond it.
MAX_ALIGN_CELLS = 10_000


@dataclass(frozen=True)
class Change:
    file: str
    path: str  # JSON pointer; list elements are addressed by id where the list has ids, else by index
    kind: str  # modified | removed | added | reordered
    before: Any = None
    after: Any = None
    #: the property name the changed value is stored under (for an element of a list: the name of the list)
    field: Any = _field(default=None, compare=False, repr=False)
    #: the objects and lists of the NEW version that contain the changed value, outermost first: one per path segment
    above: tuple = _field(default=(), compare=False, repr=False)
    #: the property name behind each path segment (``fields[i]`` is what ``above[i]`` stores that segment under)
    fields: tuple = _field(default=(), compare=False, repr=False)

    @property
    def where(self) -> str:
        return f"{self.file}{self.path}"

    def holder_path(self, depth: int) -> str:
        """Path of ``above[depth]`` (``above[0]`` is the document itself: "")."""
        return "/".join(self.path.split("/")[: depth + 1])


def same(a: Any, b: Any) -> bool:
    """Equal as JSON (Python's ``==`` says True == 1, which is a different value in a file)."""
    if a is b:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(v, b[k]) for k, v in a.items())
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def _escape(seg: Any) -> str:
    return str(seg).replace("~", "~0").replace("/", "~1")


def _is_ref_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, dict) and "id" in x and set(x) <= {"id", "note"} for x in value)


def _id_key(a: list, b: list) -> str | None:
    """The key that identifies the records of both lists (unique string values in each), if there is one."""
    items = a + b
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for key in RECORD_KEYS:
        if all(isinstance(x.get(key), str) for x in items):
            if len({x[key] for x in a}) == len(a) and len({x[key] for x in b}) == len(b):
                return key
            return None  # the lists use this key but it does not identify their records: fall back to content
    return None


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _aligned(a: list[str], b: list[str]) -> list[tuple[str, int, int, int, int]]:
    """difflib-style opcodes that turn ``a`` into ``b`` (see MAX_ALIGN_CELLS)."""
    shortest = min(len(a), len(b))
    head = 0
    while head < shortest and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < shortest - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    mid_a, mid_b = a[head:len(a) - tail], b[head:len(b) - tail]
    if not mid_a and not mid_b:
        return []
    if len(mid_a) * len(mid_b) > MAX_ALIGN_CELLS:
        return [("replace", head, head + len(mid_a), head, head + len(mid_b))]
    codes = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False).get_opcodes()
    return [(tag, i1 + head, i2 + head, j1 + head, j2 + head) for tag, i1, i2, j1, j2 in codes]


def _walk(a: Any, b: Any, file: str, path: str, key: Any, out: list[Change], field: Any = None, above: tuple = (),
          fields: tuple = ()) -> None:
    """``key``: the property name ``b`` is stored under (None for a list element); ``field``: the same, inherited by list elements."""
    if same(a, b):
        return
    if isinstance(a, dict) and isinstance(b, dict):
        inside = above + (b,)
        for k in a:
            if k not in b:
                out.append(Change(file, f"{path}/{_escape(k)}", REMOVED, before=a[k], field=k, above=inside, fields=fields + (k,)))
        for k in b:
            if k not in a:
                out.append(Change(file, f"{path}/{_escape(k)}", ADDED, after=b[k], field=k, above=inside, fields=fields + (k,)))
            else:
                _walk(a[k], b[k], file, f"{path}/{_escape(k)}", k, out, k, inside, fields + (k,))
        return
    if isinstance(a, list) and isinstance(b, list):
        if key == "sources" and _is_ref_list(a) and _is_ref_list(b):
            out.append(Change(file, path, MODIFIED, before=a, after=b, field=field, above=above, fields=fields))
            return
        inside, below = above + (b,), fields + (field,)
        id_key = _id_key(a, b)
        if id_key:
            old, new = {x[id_key]: x for x in a}, {x[id_key]: x for x in b}
            was, now = [i for i in old if i in new], [i for i in new if i in old]
            order = list(new)
            if was != now:
                out.append(Change(file, path, REORDERED, before=was, after=now, field=field, above=above, fields=fields))
            elif now and any(i not in old for i in order[:order.index(now[-1])]):
                # nothing that was there moved, but something new stands in front of it
                out.append(Change(file, path, REORDERED, before=was, after=order, field=field, above=above, fields=fields))
            for ident, x in old.items():
                if ident not in new:
                    out.append(Change(file, f"{path}/{_escape(ident)}", REMOVED, before=x, field=field, above=inside, fields=below))
            for ident, x in new.items():
                if ident not in old:
                    out.append(Change(file, f"{path}/{_escape(ident)}", ADDED, after=x, field=field, above=inside, fields=below))
                else:
                    _walk(old[ident], x, file, f"{path}/{_escape(ident)}", None, out, field, inside, below)
            return
        fresh: set[int] = set()
        found: list[Change] = []
        for tag, i1, i2, j1, j2 in _aligned([_canon(x) for x in a], [_canon(x) for x in b]):
            if tag == "equal":
                continue
            paired = min(i2 - i1, j2 - j1)
            for n in range(paired):
                _walk(a[i1 + n], b[j1 + n], file, f"{path}/{i1 + n}", None, found, field, inside, below)
            for i in range(i1 + paired, i2):
                found.append(Change(file, f"{path}/{i}", REMOVED, before=a[i], field=field, above=inside, fields=below))
            for j in range(j1 + paired, j2):
                fresh.add(j)
                found.append(Change(file, f"{path}/{j}", ADDED, after=b[j], field=field, above=inside, fields=below))
        kept = [j for j in range(len(b)) if j not in fresh]
        if fresh and kept and min(fresh) < kept[-1]:
            # an entry that is new and stands in front of one that was there (a note put first, a record inserted)
            numbered = iter(range(len(kept)))
            out.append(Change(file, path, REORDERED, before=[str(n) for n in range(len(kept))],
                              after=[NEW_ENTRY if j in fresh else str(next(numbered)) for j in range(len(b))],
                              field=field, above=above, fields=fields))
        out.extend(found)
        return
    if isinstance(a, (dict, list)):
        # an object or a list replaced by something of another kind: everything that was there is gone
        out.append(Change(file, path, REMOVED, before=a, field=field, above=above, fields=fields))
        out.append(Change(file, path, ADDED, after=b, field=field, above=above, fields=fields))
        return
    out.append(Change(file, path, MODIFIED, before=a, after=b, field=field, above=above, fields=fields))


def diff_value(before: Any, after: Any, file: str = "", path: str = "", key: Any = None) -> list[Change]:
    """The changes between two values (``key``: the property name the value is stored under, if any)."""
    out: list[Change] = []
    _walk(before, after, file, path, key, out, key, ())
    return out


def diff_docs(before: dict[str, Any], after: dict[str, Any], files: Iterable[str] | None = None) -> list[Change]:
    """The changes between two sets of documents (relative path -> parsed JSON), in file order."""
    out: list[Change] = []
    for rel in sorted(set(before) | set(after)) if files is None else files:
        if rel not in before:
            out.append(Change(rel, "", ADDED, after=after[rel]))
        elif rel not in after:
            out.append(Change(rel, "", REMOVED, before=before[rel]))
        else:
            _walk(before[rel], after[rel], rel, "", None, out)
    return out


def count(changes: Iterable[Change], *kinds: str) -> int:
    return sum(1 for c in changes if not kinds or c.kind in kinds)
