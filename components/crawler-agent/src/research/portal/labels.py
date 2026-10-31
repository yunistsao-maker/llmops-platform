"""What a change claims besides its value: which ``verification`` label stands behind it, what it stamps, and whether
it is of a kind that can be compared with a quote at all.

Read from a :class:`~research.portal.diff.Change` - the real difference - by the agent's validator and by the
publishing pipeline alike, so that both answer "is this fact labelled as derived?", "does this say the record was
re-checked today?", "did this record lose a citation?" and "is this a value nobody can check against a quote?" from
the same code.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Iterator

from .diff import ADDED, MODIFIED, REMOVED, REORDERED, Change, same

LABEL = "verification"
#: where the deep dives are (one file per catalog model: data/models/<id>.json)
DEEP_DIVES = "data/models/"
DERIVED = "derived"
#: fields that annotate a record rather than state its core fact; changing them does not re-assert the record's label
ANNOTATION_KEYS = frozenset({
    "notes", "note", "scopeNote", "sources", "conflicts", "replacement", "summary", "detail", "title", "caution",
    "highlights", "onboardingNotes", "regionNotes", "retentionNotes", "behaviours", "programmes", "classifications",
    "strengths", "weaknesses", "bestFor", "avoidFor", "tags", "lastVerified", "asOf",
})


#: string fields that hold a software version: a LiteLLM release, the version an advisory is fixed in, a release line
VERSION_KEYS = frozenset({"version", "versions", "fixedIn", "affected", "recommendedVersion", "minVersionBundledCostMap", "minVersionParams",
                          "minVersionRouting", "supportedLines"})
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: A software version as release notes and package indexes write it: v1.102.1, 1.82.7, v1.103.0-rc.1, 1.103.0rc1,
#: v1.83.10-stable, v1.74.9-stable.patch.1, 1.83.0.dev1, a line (1.102, v1.102.x).  At least two numbers: "v2" or "1." in a
#: version field is not a version anybody can look up in a quote.  A version is taken whole or not at all: the numbers
#: ("1.40.0" is not the line "1.40"), and every tag that is joined on - "-rc1", "-stable.patch.1", "-preview", "+build5",
#: also one this code has never heard of: v1.103.0-RC1 is not v1.103.0, and "1.40.0foo" is not 1.40.0.
VERSION_TOKEN = re.compile(r"(?<![0-9A-Za-z.])[vV]?([0-9]+(?:\.[0-9]+)+)(?![0-9]|\.[0-9])(\.[xX*])?"
                           r"((?:[-+.]?[A-Za-z][A-Za-z0-9]*(?:[.-][0-9]+)*)*)(?![0-9A-Za-z])")
_TAG_PART = re.compile(r"[a-z]+|[0-9]+")
#: what stands between the versions of a specification ("1.82.7、1.82.8", "v1.81.16 – v1.83.6", "< v1.84.0", "≤ v1.99.x")
_SPEC_GLUE = re.compile(r"[\s,、，;；<>≤≥=~^–—\-()（）]*")


def _tags(chain: str) -> str:
    """The tags joined on to a version, in one spelling: "-rc.1" and "rc1" are "rc1", "-stable.patch.1" is "stable-patch1"."""
    out: list[str] = []
    for part in _TAG_PART.findall(chain.lower()):
        if part.isdigit() and out:
            out[-1] += part
        else:
            out.append(part)
    return "-".join(out)


#: marks that hang on a number and are not part of it (a footnote mark, mostly): NFKC would make "1.84.1²" the release 1.84.12
_HANGING = re.compile("[\u00b2\u00b3\u00b9\u2070-\u209c\u2460-\u24ff\u2776-\u2793\u3251-\u325f\u32b1-\u32bf]")


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", _HANGING.sub(" ", text))


def version_spans(text: Any) -> list[tuple[int, int, str]]:
    """(start, end, version) of the software versions written in ``text`` - which is read as it is: a caller that wants
    full-width letters read as ASCII folds the text first (``versions_in`` does)."""
    out = []
    for m in VERSION_TOKEN.finditer(text) if isinstance(text, str) else ():
        core, wildcard, chain = m.groups()
        tags = _tags(chain)
        out.append((m.start(), m.end(), core + (".x" if wildcard else "") + (f"-{tags}" if tags else "")))
    return out


def versions_in(text: Any) -> list[str]:
    """The software versions written in ``text``, in one spelling: without the leading "v", the tags as ``-rc1`` /
    ``-stable`` / ``-stable-patch1`` whatever joins them in the text; full-width letters read as ASCII."""
    return [version for _start, _end, version in version_spans(_folded(text))] if isinstance(text, str) else []


def whole_version(value: Any) -> bool:
    """Is ``value`` a version, or a specification made of versions, and nothing else?  ("v1.102.1", "1.82.7、1.82.8",
    "< v1.84.0" - not a sentence that mentions a version.)"""
    if not isinstance(value, str) or _HANGING.search(value):
        return False
    text = _folded(value)
    rest, found = VERSION_TOKEN.subn(" ", text)
    return found > 0 and _SPEC_GLUE.fullmatch(rest) is not None


def one_version(value: Any) -> tuple[str, str] | None:
    """(what stands around it, the version) when ``value`` is ONE version and nothing else but the signs of a
    specification ("v1.102.1", "< v1.84.0"); None for anything else - a list or a range of versions among them."""
    if not isinstance(value, str) or _HANGING.search(value):
        return None
    text = _folded(value)
    found = list(VERSION_TOKEN.finditer(text))
    if len(found) != 1:
        return None
    before, after = text[:found[0].start()], text[found[0].end():]
    if _SPEC_GLUE.fullmatch(before) is None or _SPEC_GLUE.fullmatch(after) is None:
        return None
    return "".join(before.split()) + "\x00" + "".join(after.split()), versions_in(text)[0]


def _version_parts(version: str) -> tuple[tuple[str, ...], bool, str]:
    core, _, suffix = version.partition("-")
    numbers = core.split(".")
    wildcard = numbers[-1] in ("x", "*")
    return tuple(numbers[:-1] if wildcard else numbers), wildcard, suffix


def version_backed(version: str, quoted: Iterable[str]) -> bool:
    """Is ``version`` stated by one of the ``quoted`` versions?  (Both as ``versions_in`` spells them.)

    The same version (a release tagged ``-stable`` is that release); or, for a release line ("1.102", "1.102.x"), any
    release of that line - a final or a stable one: a release candidate, a nightly or a build with a tag nobody knows
    says nothing about the line.  A release candidate is not the release, a patched build ("-stable.patch.1") is not
    the build it patches, and "1.102.1" is not backed by "1.102.10".
    """
    core, wildcard, suffix = _version_parts(version)
    for q in quoted:
        q_core, q_wild, q_suffix = _version_parts(q)
        if q_core == core and q_wild == wildcard and (q_suffix == suffix or (not suffix and q_suffix == "stable")):
            return True
        if (wildcard or (len(core) == 2 and not suffix)) and q_core[:len(core)] == core and q_suffix.split("-")[0] in ("", "stable"):
            return True
    return False


def written(changes: Iterable[Change]) -> list[Change]:
    """The changes that put a value into the data (an addition or a new value for something that existed)."""
    return [c for c in changes if c.kind in (ADDED, MODIFIED)]


def label_of(node: Any) -> str | None:
    value = node.get(LABEL) if isinstance(node, dict) else None
    return value if isinstance(value, str) else None


def nearest_label(chain: Iterable[Any]) -> str | None:
    """The label of the innermost labelled object in ``chain`` (outermost first)."""
    found = None
    for node in chain:
        found = label_of(node) or found
    return found


def _holder_depth(change: Change) -> int | None:
    for depth in range(len(change.above) - 1, -1, -1):
        if label_of(change.above[depth]) is not None:
            return depth
    return None


def _own_labels(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        if label_of(value) is not None:
            yield f"{path}/{LABEL}", value[LABEL]
        for k, v in value.items():
            yield from _own_labels(v, f"{path}/{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _own_labels(v, f"{path}/{i}")


def labels_behind(change: Change, annotations: frozenset = frozenset()) -> list[tuple[str, str]]:
    """(where, label) for every label this change asserts or leans on.

    A label that is written or changed asserts itself.  A value that carries no label of its own leans on the nearest
    labelled object around it - unless it is, or sits inside, one of that object's ``annotations`` (a note added to an
    official fact does not claim to be official, and neither does an entry of its ``conflicts`` history).
    """
    if change.kind not in (ADDED, MODIFIED):
        return []
    if change.field == LABEL and not isinstance(change.after, (dict, list)):
        return [(change.path, change.after)] if isinstance(change.after, str) else []
    out = list(_own_labels(change.after, change.path))
    if label_of(change.after) is None:
        depth = _holder_depth(change)
        if depth is not None and not any(name in annotations for name in change.fields[depth:]):
            out.append((f"{change.holder_path(depth)}/{LABEL}", change.above[depth][LABEL]))
    return out


def derived(changes: Iterable[Change]) -> list[str]:
    """The changes that write something labelled as computed rather than quoted (file + path; annotations included),
    or that take that label off: what was computed does not become a quotation by being relabelled."""
    out = [c.where for c in written(changes) if any(label == DERIVED for _where, label in labels_behind(c))]
    out += [c.where for c in changes if c.field == LABEL and c.kind in (MODIFIED, REMOVED) and c.before == DERIVED and c.where not in out]
    return out


def relabelled(changes: Iterable[Change]) -> list[tuple[str, Any, Any]]:
    """(where, label before, label after) for every ``verification`` label of an existing record that is changed, added
    or taken away.  A new record's label is part of the new record and is not listed."""
    out = []
    for c in changes:
        if c.field == LABEL and not isinstance(c.after, (dict, list)) and not isinstance(c.before, (dict, list)):
            if c.kind in (MODIFIED, REMOVED) or (c.kind == ADDED and c.above and isinstance(c.above[-1], dict)):
                out.append((c.where, c.before if c.kind != ADDED else None, c.after if c.kind != REMOVED else None))
    return out


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def blanked(changes: Iterable[Change]) -> list[str]:
    """Where text that was there is replaced by nothing ("" or blanks, or null): a deletion spelled as a change."""
    return [c.where for c in changes if c.kind == MODIFIED and isinstance(c.before, str) and c.before.strip() and _blank(c.after)]


def kind_of(value: Any, field: Any) -> str | None:
    """"number", "date" or "version" for a value of a kind that can be looked up in a quote; None for anything else.

    Under a version field a value is a version when it is nothing but versions ("v1.102.1", "< v1.84.0") - a sentence
    that mentions one is text, and so is a model's version that happens to look like a date ("2024-05-13").
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        if field in VERSION_KEYS:
            return "version" if whole_version(value) else None
        if _ISO_DATE.fullmatch(value) is not None:
            return "date"
    return None


def is_typed(change: Change) -> bool:
    """Is this the kind of change that is compared with the cited quotes: a number, a date or a version that is added,
    or one that takes the place of a value OF THE SAME KIND?

    It is a property of the change, not of the new value.  A date written where a paragraph, a model name or a status
    stood is not "a date that changed": it is that text, name or status replaced - by something that merely looks
    checkable.  And of a version field, only "this version instead of that one" is a version that changed: in a list
    or a range of versions, and in "< 1.84.0" that becomes "≥ 1.84.0", what changes is what the field says, with every
    version in it still to be found in a quote.
    """
    kind = kind_of(change.after, change.field)
    if kind is None:
        return False
    if change.kind != MODIFIED or change.before is None:
        return True  # (a field that held null held nothing: the value is added)
    if kind_of(change.before, change.field) != kind:
        return False
    if kind == "version":
        was, now = one_version(change.before), one_version(change.after)
        return was is not None and now is not None and was[0] == now[0]
    return True


def unquotable(changes: Iterable[Change], annotations: frozenset = ANNOTATION_KEYS) -> list[Change]:
    """Changes that write a single value into a record that exists, where the value is neither of a kind the cited
    quotes are searched for (``is_typed``) nor free text of the record (``annotations``): a status, a yes/no, a
    category, an identifier.  Nothing compares such a value with what a page says, so it is not for a machine alone.

    A whole new record (an added object) is not listed: what it is about and its dates and numbers are checked, and
    there is no earlier value that it silently replaces.
    """
    out = []
    for c in written(changes):
        if isinstance(c.after, (dict, list)) or c.field == LABEL or not c.above:
            continue
        if is_typed(c) or any(name in annotations for name in c.fields if isinstance(name, str)):
            continue
        out.append(c)
    return out


def reworded(changes: Iterable[Change], annotations: frozenset = ANNOTATION_KEYS) -> list[Change]:
    """Changes that put other words where a record had words: a note, a summary, a description that was there and reads
    differently now.  Nothing compares free text with a page.  Adding to it is one thing; replacing what the portal says
    - what a person may have written - is another, and is not for a machine alone."""
    out = []
    for c in changes:
        if (c.kind == MODIFIED and isinstance(c.before, str) and isinstance(c.after, str) and c.before.strip() and c.after.strip()
                and c.field != LABEL and not is_typed(c) and any(name in annotations for name in c.fields if isinstance(name, str))):
            out.append(c)
    return out


def put_in_front(change: Change) -> bool:
    """Is this new order one in which nothing that was there moved - new entries were put in front of old ones?  (The
    other kind of new order: the entries that were there stand in another order.)"""
    if change.kind != REORDERED or not isinstance(change.before, list) or not isinstance(change.after, list):
        return False
    return [x for x in change.after if x in change.before] == change.before


def recited(changes: Iterable[Change]) -> list[str]:
    """Where the citations a fact already has are touched: another order, a citation's note reworded or taken away, a
    citation swapped or dropped.  A list of citations may grow at its end; what stands in it is what somebody checked
    the fact against, and in which order of importance."""
    out = []
    for c in changes:
        if c.kind == MODIFIED and c.field == "sources" and isinstance(c.before, list) and isinstance(c.after, list) and c.before:
            if c.after and not same(c.after[:len(c.before)], c.before):
                out.append(c.where)
    return out


def dropped_citations(changes: Iterable[Change]) -> list[str]:
    """Where a record keeps citing something but no longer cites a page it cited before (a citation was swapped out)."""
    out = []
    for c in changes:
        if c.kind == MODIFIED and c.field == "sources" and isinstance(c.before, list) and isinstance(c.after, list) and c.after:
            was = {r.get("id") for r in c.before if isinstance(r, dict)}
            now = {r.get("id") for r in c.after if isinstance(r, dict)}
            if was - now:
                out.append(c.where)
    return out


def _fields(value: Any, name: str, path: str, holder: Any) -> Iterator[tuple[str, Any, Any]]:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == name and not isinstance(v, (dict, list)):
                yield path, v, value
            else:
                yield from _fields(v, name, f"{path}/{k}", value)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _fields(v, name, f"{path}/{i}", holder)


def stamped(change: Change, name: str) -> list[tuple[str, Any, Any]]:
    """(path of the record, value, record) for every field called ``name`` that this change writes."""
    if change.kind not in (ADDED, MODIFIED):
        return []
    if change.field == name and not isinstance(change.after, (dict, list)):
        if not change.above or not isinstance(change.above[-1], dict):
            return []  # an element of a list that is itself called ``name``: not a record's field
        return [(change.holder_path(len(change.above) - 1), change.after, change.above[-1])]
    return list(_fields(change.after, name, change.path, None))


def source_ids(record: Any) -> list[str]:
    """Ids of every source reference inside ``record`` (sorted, unique)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            refs = node.get("sources")
            if isinstance(refs, list):
                found.update(r["id"] for r in refs if isinstance(r, dict) and isinstance(r.get("id"), str) and set(r) <= {"id", "note"})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(record)
    return sorted(found)


def new_deep_dives(changes: Iterable[Change]) -> list[str]:
    """The deep dives that come into being: a data/models/<id>.json that was not there and is now (the file, as a whole).

    A deep dive is mostly prose - what is new about the model, what breaks, how to onboard it, where it is strong - and
    its dates, numbers and versions are the least of it.  Its every value is new, and what no quote can be compared
    with is all of the text: a new one is not for a machine alone, however well each figure in it is quoted."""
    return [c.file for c in changes if c.kind == ADDED and c.path == "" and c.file.startswith(DEEP_DIVES)]


def emptied_citations(changes: Iterable[Change]) -> list[str]:
    """Where a list of citations that had entries is left empty."""
    return [c.where for c in changes
            if c.kind == MODIFIED and c.field == "sources" and isinstance(c.before, list) and c.before and c.after == []]
