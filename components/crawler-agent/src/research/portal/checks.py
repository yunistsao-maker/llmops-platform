r"""Portal validation: JSON Schema + the cross-file checks from the portal's tools/validate_data.py.

This is a port of the portal's own validator (same file->definition table, same cross-file rules),
operating on in-memory documents so a *proposal can be validated before anything is written*.

A few rules go beyond the portal's validator, because they guard what is published without a person looking:

* numbers must be finite and no larger than a browser can read exactly - Python reads the JSON literal ``1e999`` as
  infinity and would write it back as ``Infinity``, which no browser can parse, and it keeps an integer of 400 digits
  that a browser reads as infinity;
* link fields (``url``, ``...Url`` - what the UI renders as a link) must be plain ``https://`` URLs;
* ``date-time`` values are checked here with the standard library, so the result does not depend on whether an
  optional package of the JSON Schema library happens to be installed;
* a ``pattern`` of the schema means what it means to the browser that reads the data (ECMAScript): ``$`` is the end of
  the value and ``\d`` is an ASCII digit.  Python's own reading lets ``"2026-10-02\n"`` and full-width digits through
  ``^\d{4}-\d{2}-\d{2}$``, and such a "date" is one no date rule recognises and no browser can parse;
* text holds no control characters other than tab and line breaks, and no half of a surrogate pair;
* the values of data/litellm.json that the Gateway page computes with (versions, release lines, the versions to avoid)
  are in the spellings that page can read (``gateway_problems``).

Everything is reported as a complete list: callers compare the problems before and after a change, and a list cut
short would hide new problems (or invent them).  Shorten only for display (``shorten``).
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import functools
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

from jsonschema import Draft7Validator, FormatChecker, validators
from jsonschema.exceptions import ValidationError

from .patch import record_key

FILE_TYPES = [
    ("data/sources.json", "SourcesFile"),
    ("data/catalog.json", "CatalogFile"),
    ("data/lifecycle.json", "LifecycleFile"),
    ("data/litellm.json", "LiteLLMFile"),
    ("data/requirements.json", "RequirementsFile"),
    ("data/digests/*.json", "DigestFile"),
    ("data/models/*.json", "DeepDive"),
    ("data/open-models.json", "OpenModelsFile"),
]
MAX_URL_CHARS = 2048


ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Problem:
    """Something a validator objects to.

    ``severity``: an ERROR is data no page can be trusted to work with - nothing publishes it without an explicit
    override.  A WARNING is data that is usable, in which a value will not do what its author meant (the Gateway page
    cannot compute with it, two records answer to the same key, a character that is a line break to some programs):
    a person who publishes a checkout is told and decides; a proposal may not ADD one (to the validators of
    proposals every new problem is a refusal, whatever its severity).
    """

    file: str
    where: str
    message: str
    severity: str = ERROR

    def __str__(self) -> str:
        return f"{self.file}: {self.where + ': ' if self.where else ''}{self.message}"


def blocking(problems: Iterable["Problem"]) -> list["Problem"]:
    """The problems that stop a person's own publish (everything but the warnings)."""
    return [p for p in problems if p.severity != WARNING]


def advisory(problems: Iterable["Problem"]) -> list["Problem"]:
    return [p for p in problems if p.severity == WARNING]


def definition_for(rel: str) -> str | None:
    for pattern, definition in FILE_TYPES:
        if fnmatch.fnmatch(rel, pattern):
            return definition
    return None


# ---------------------------------------------------------------------------------------------- formats
#: The formats checked here, with the standard library only.  jsonschema's own "date-time" check needs an optional
#: package and silently accepts everything without it, so what counts as valid would depend on the installation.
FORMATS = FormatChecker(formats=())
# [0-9], not \d: Python's \d also matches the digits of every other script ("２０２６"), which nothing downstream can parse
_RFC3339 = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?([Zz]|[+-]([0-9]{2}):([0-9]{2}))")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def is_datetime(value: Any) -> bool:
    """An RFC 3339 timestamp (``2026-10-05T03:00:00Z``, ``...+08:00``); non-strings are not this check's business."""
    if not isinstance(value, str):
        return True
    m = _RFC3339.fullmatch(value)
    if not m:
        return False
    try:
        dt.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]))
    except ValueError:
        return False
    return m[9] is None or (int(m[9]) <= 23 and int(m[10]) <= 59)


def is_date(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    if not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


FORMATS.checks("date-time")(is_datetime)
FORMATS.checks("date")(is_date)


# ---------------------------------------------------------------------------------------------- patterns
@functools.lru_cache(maxsize=512)
def _ecma(pattern: str) -> "re.Pattern[str] | None":
    """``pattern`` (written for ECMAScript, as JSON Schema says) as a Python expression that means the same.

    ``$`` becomes ``\\Z`` (Python's ``$`` also matches before a final line break), ``\\d`` and ``\\w`` become the ASCII
    classes they are in ECMAScript.  None when the result does not compile (a construct only one of the two languages has).
    """
    out: list[str] = []
    in_class = ascii_only = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if nxt == "d":
                out.append("0-9" if in_class else "[0-9]")
            elif nxt == "w":
                out.append("A-Za-z0-9_" if in_class else "[A-Za-z0-9_]")
            else:
                if nxt in "DWbB":  # cannot be spelled out inside a class: compile the whole pattern as ASCII instead
                    ascii_only = True
                out.append(ch + nxt)
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "$":
            out.append(r"\Z")
            i += 1
            continue
        out.append(ch)
        i += 1
    try:
        return re.compile("".join(out), re.ASCII if ascii_only else 0)
    except re.error:
        return None


def pattern_matches(pattern: str, text: str) -> bool:
    """Does ``text`` match the schema ``pattern``, read the way a browser reads it?"""
    rx = _ecma(pattern)
    if rx is not None:
        return rx.search(text) is not None
    if not re.search(pattern, text):
        return False
    # the pattern could not be translated: at least a "$" must not be satisfied by a line break at the very end
    return not text.endswith("\n") or re.search(pattern, text + "\n") is not None


def _pattern(validator: Any, patrn: Any, instance: Any, schema: Any) -> Iterator[ValidationError]:
    if validator.is_type(instance, "string") and not pattern_matches(patrn, instance):
        yield ValidationError(f"{instance!r} does not match {patrn!r}")


#: Draft 7 with ``pattern`` read as ECMAScript reads it
PortalValidator = validators.extend(Draft7Validator, {"pattern": _pattern})


# ---------------------------------------------------------------------------------------------- JSON values
def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def non_finite(node: Any, path: str = "") -> Iterator[str]:
    """Paths of the numbers JSON cannot represent (NaN, Infinity, -Infinity)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from non_finite(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from non_finite(v, f"{path}/{i}")
    elif isinstance(node, float) and not math.isfinite(node):
        yield path or "/"


#: the largest magnitude a browser reads exactly (Number.MAX_SAFE_INTEGER); portal numbers are prices, limits and scores
MAX_NUMBER = 2**53 - 1


def too_large(node: Any, path: str = "") -> Iterator[str]:
    """Paths of the numbers a browser cannot read as written (an integer of 400 digits is valid JSON and reads as Infinity)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from too_large(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from too_large(v, f"{path}/{i}")
    elif isinstance(node, (int, float)) and not isinstance(node, bool) and not (isinstance(node, float) and not math.isfinite(node)):
        if abs(node) > MAX_NUMBER:
            yield path or "/"


#: C0 controls except tab and the line breaks, DEL, and lone surrogates: never text
_BAD_TEXT = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
#: the C1 controls (U+0085 NEXT LINE among them: Python strips it as white space, a browser does not) and the Unicode
#: line and paragraph separators: a line break to some programs and not to others
_ODD_BREAKS = re.compile("[\x80-\x9f\u2028\u2029]")


def text_problems(rel: str, node: Any) -> list[Problem]:
    """Strings (values and property names) with a control character or half a surrogate pair in them.

    Tab and line breaks are text (code samples have them); nothing else below U+0020 ever is, and a lone surrogate
    cannot even be written to a file.  The C1 controls and the Unicode line / paragraph separators do get into text
    that was copied from a web page; they are reported as a WARNING - a value that one program reads as "v1.84.1" and
    the next one does not is worth knowing about, and is not what a proposal may add.
    """
    out: list[Problem] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(k, str) and (_BAD_TEXT.search(k) or "\n" in k or "\r" in k):
                    out.append(Problem(rel, path, f"property name {json.dumps(k[:60], ensure_ascii=True)} contains a control character"))
                elif isinstance(k, str) and _ODD_BREAKS.search(k):
                    out.append(Problem(rel, path, f"property name {json.dumps(k[:60], ensure_ascii=True)} contains a C1 control character or a "
                                                  "Unicode line / paragraph separator", WARNING))
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")
        elif isinstance(value, str):
            if _BAD_TEXT.search(value):
                out.append(Problem(rel, path, "contains a control character (or half of a surrogate pair) that is not text"))
            elif _ODD_BREAKS.search(value):
                out.append(Problem(rel, path, "contains a C1 control character or a Unicode line / paragraph separator (U+0080-U+009F, "
                                              "U+2028, U+2029): a line break to some programs and not to others", WARNING))

    walk(node, "")
    return out


def loads_strict(raw: str | bytes) -> Any:
    """``json.loads`` that accepts only what every JSON parser accepts.

    Python's parser also reads ``NaN`` / ``Infinity`` and turns ``1e999`` into infinity; a file written from such a value
    is not JSON any more.  Raises ValueError.
    """
    value = json.loads(raw, parse_constant=_reject_constant)
    bad = next(non_finite(value), None)
    if bad is not None:
        raise ValueError(f"number at {bad} is too large to be stored as JSON (it reads as Infinity)")
    return value


# ---------------------------------------------------------------------------------------------- links
#: property names that hold a link the UI renders as one (the schema has: url, modelCardUrl, systemCardUrl)
_URL_KEY = re.compile(r"url|uri|href|.*(?:Url|URL|Uri|URI|Href|_url|_uri|_href)")
_INVISIBLE = re.compile(r"[\x00-\x20\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202f\u2060-\u2069\ufeff]")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")


def is_url_key(key: Any) -> bool:
    return isinstance(key, str) and _URL_KEY.fullmatch(key) is not None


def https_url_problem(value: str) -> str | None:
    """Why ``value`` is not a plain https link (None when it is one)."""
    if len(value) > MAX_URL_CHARS:
        return f"is longer than {MAX_URL_CHARS} characters"
    if _INVISIBLE.search(value):
        return "contains whitespace or invisible characters"
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return "is not a well-formed URL"
    if parts.scheme != "https":
        return "must start with https://"
    if "@" in parts.netloc:
        return "must not contain a user name or password (the part before '@')"
    if not _HOST.fullmatch(parts.hostname or ""):
        return "needs a host name such as docs.example.com"
    if port is not None and not 0 < port < 65536:
        return "has an invalid port"
    return None


def link_problems(rel: str, node: Any) -> list[Problem]:
    """Link fields that are not plain https URLs (``javascript:``, ``data:``, ``http://``, a look-alike with ``user@``...).

    The value is part of the message and the position is not, so the same link is the same problem wherever it moves.
    """
    out: list[Problem] = []

    def walk(value: Any, key: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and is_url_key(key):
            why = https_url_problem(value)
            if why:
                out.append(Problem(rel, "", f"link field {key!r} {why}: {json.dumps(value[:120], ensure_ascii=True)}"))

    walk(node, None)
    return out


# ---------------------------------------------------------------------------------------------- schema
def validator_for(schema: dict, definition: str) -> Draft7Validator:
    sub = {"$schema": schema.get("$schema"), "definitions": schema["definitions"], "$ref": f"#/definitions/{definition}"}
    return PortalValidator(sub, format_checker=FORMATS)


#: schema errors of one document that are listed one by one (the rest are counted: see validate_definition)
MAX_SCHEMA_PROBLEMS = 200


def validate_definition(schema: Any, definition: str, instance: Any, label: str = "") -> list[Problem]:
    """The schema errors of ``instance`` against ``#/definitions/<definition>`` (a schema that cannot be used is a problem too).

    The first ``MAX_SCHEMA_PROBLEMS`` are listed; what comes after them is one more problem that says how many there
    are and carries a fingerprint of them.  A list of numbers where a list of texts belongs is one mistake with a
    hundred thousand errors: keeping every one of them (and comparing them, and printing them) is what takes the
    memory.  The fingerprint is there for :func:`new_problems`: whatever a change adds to the errors of a document, the
    problems of before and after differ - in a listed error, or in that last line.
    """
    name = label or definition
    definitions = schema.get("definitions") if isinstance(schema, dict) else None
    if not isinstance(definitions, dict) or definition not in definitions:
        return [Problem(name, "", f"the schema has no definition {definition!r}")]
    listed: list[tuple[list[str], str]] = []
    more = mark = 0
    try:
        for e in validator_for(schema, definition).iter_errors(instance):
            path, message = [str(p) for p in e.absolute_path], e.message[:240]
            if len(listed) < MAX_SCHEMA_PROBLEMS:
                listed.append((path, message))
            else:  # (a sum: the same errors found in another order are the same errors)
                more += 1
                mark = (mark + int.from_bytes(hashlib.sha256(json.dumps([path, message]).encode("ascii")).digest()[:8], "big")) % 2**64
    except Exception as e:  # a broken $ref or keyword inside the schema itself
        return [Problem(name, "", f"the schema cannot be evaluated ({type(e).__name__}: {str(e)[:200]})")]
    listed.sort(key=lambda item: item[0])
    problems = [Problem(name, "/".join(path) or "(root)", message) for path, message in listed]
    if more:
        problems.append(Problem(name, "", f"… and {more:,} more schema errors that are not listed one by one (fingerprint {mark:016x})"))
    return problems


def iter_source_refs(node: Any, path: str = "") -> Iterator[tuple[Any, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "sources" and isinstance(v, list) and all(isinstance(x, dict) and "id" in x and set(x) <= {"id", "note"} for x in v):
                for x in v:
                    yield x["id"], f"{path}.{k}"
            else:
                yield from iter_source_refs(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_source_refs(v, f"{path}[{i}]")


def iter_dates(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_dates(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_dates(v, f"{path}[{i}]")
    elif isinstance(node, str) and len(node) == 10 and node[4] == "-" and node[7] == "-" and node[:4].isdigit():
        yield node, path


def _records(doc: Any, key: str) -> list[dict]:
    """The object entries of ``doc[key]`` (nothing when ``doc`` is not an object or ``doc[key]`` is not a list)."""
    items = doc.get(key) if isinstance(doc, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _duplicates(problems: list["Problem"], rel: str, label: str, ids: list[Any]) -> None:
    hashable = [i for i in ids if isinstance(i, (str, int))]
    dup = sorted({i for i in hashable if hashable.count(i) > 1}, key=str)
    if dup:
        problems.append(Problem(rel, "", f"duplicate {label} ids: {dup}"))


def duplicate_records(rel: str, doc: Any) -> list[Problem]:
    """Lists in which two records carry the same identifying key (``patch.record_key``): a page that looks a record up
    by that key shows one of the two, and an operation cannot address either of them by it.  A WARNING: data may
    hold two price sheets for one scope on purpose - a person can publish that; a proposal cannot add the second one.

    (Where: the path of the list, with ``*`` for a step through a record that has no key - so that the same finding
    reads the same after another record was added in front of it.)"""
    out: list[Problem] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            seen: dict[tuple[str, str], int] = {}
            for el in node:
                ident = record_key(el)
                if ident is not None:
                    seen[ident] = seen.get(ident, 0) + 1
            for (key, value), n in sorted(seen.items()):
                if n > 1:
                    out.append(Problem(rel, path.lstrip("/"), f"{n} records have {key} = {json.dumps(value[:80], ensure_ascii=False)}: "
                                                              "a record is looked up by it, so only one should carry it", WARNING))
            for el in node:
                ident = record_key(el)
                walk(el, f"{path}/{ident[1] if ident else '*'}")

    walk(doc, "")
    return out


def _bad_dates(rel: str, doc: Any) -> list[Problem]:
    out = []
    for d, where in iter_dates(doc):
        try:
            date.fromisoformat(d)
        except ValueError:
            out.append(Problem(rel, where, f"invalid date '{d}'"))
    return out


# ---------------------------------------------------------------------------------------------- what pages compute with
# The Gateway page of the portal answers "may I run this LiteLLM version?" from data/litellm.json.  It parses
# recommended.version and minimumBaseline.version as three numbers (and fails on anything else), looks the user's
# release line up in supportedLines as "v<major>.<minor>.x", and reads avoid[].versions as a list - separated by "、" or
# "," - of single versions, ranges ("a – b", "< a", "≤ 1.82.x") and, failing those, tags that it searches for inside
# the version the user typed.  To the schema all of these are strings.
_SEMVER = r"[vV]?[0-9]+\.[0-9]+\.[0-9]+"
#: a release as it is tagged: v1.102.1, v1.103.0-rc.1, 1.103.0rc1, v1.83.10-stable, v1.74.9-stable.patch.1 (tags can be stacked)
_RELEASE = re.compile(_SEMVER + r"(?:[-.]?(?:rc|alpha|beta|dev|nightly|stable|post|patch)(?:[.-]?[0-9]+)?)*")
_RELEASE_LINE = re.compile(r"v[0-9]+\.[0-9]+\.x")
_AVOID_PART = re.compile(rf"{_SEMVER}\s*[–—-]\s*{_SEMVER}|<\s*{_SEMVER}|≤\s*[vV]?[0-9]+\.[0-9]+\.x|{_SEMVER}")


def _avoid_part_problem(part: str) -> tuple[str, str] | None:
    """(what is wrong with one entry of an ``avoid[].versions`` list, how bad that is) - None when the page reads it
    as it is meant.

    What the page cannot read as a version or a range it looks for, as a tag, inside the version a user types.  An
    entry that begins like a version and is not one, or that is too short, is found in versions it was never meant
    for: an ERROR.  An entry that begins with a comparison sign and is not one of the ranges the page knows ("≥ 1.90.0",
    "< v1.101.0-rc.1 (when ...)") is found in none: the page only shows it - a WARNING.
    """
    if _AVOID_PART.fullmatch(part):
        return None
    tag = part.replace("*", "")
    if re.match(r"[vV]?[0-9]", tag):
        return "is not a version (1.82.7), a range (1.82.7 – 1.82.8, < 1.83.7, ≤ 1.82.x) or a tag (*-rc)", ERROR
    if re.match(r"[<≤>≥=~^!]", tag):
        return ("is not a range the Gateway page can compute with (it reads 1.82.7 – 1.82.8, < 1.83.7 and ≤ 1.82.x, each with "
                "nothing else in the entry): the page will show this entry and never match a version against it. Put the "
                "condition into `reason`", WARNING)
    if len(tag) < 3 or not re.search(r"[A-Za-z]", tag):
        return "is too short to be a tag: the page would find it inside almost any version", ERROR
    return None


def gateway_problems(rel: str, doc: Any) -> list[Problem]:
    """Values of data/litellm.json that the Gateway page cannot compute with, or would read as "every version"."""
    out: list[Problem] = []
    if not isinstance(doc, dict):
        return out

    def shown(value: str) -> str:
        # as written, except what cannot be seen (a control character, a zero-width one): that is spelled out
        return "".join(ch if ch.isprintable() else f"\\u{ord(ch):04x}" for ch in json.dumps(value[:60], ensure_ascii=False))

    for key in ("recommended", "minimumBaseline"):
        version = doc[key].get("version") if isinstance(doc.get(key), dict) else None
        if isinstance(version, str) and not _RELEASE.fullmatch(version):
            out.append(Problem(rel, f"{key}/version", f"{shown(version)} is not a release version such as v1.102.1 (the Gateway page compares it as one)"))
    lines = doc.get("supportedLines")
    for line in lines if isinstance(lines, list) else []:
        if isinstance(line, str) and not _RELEASE_LINE.fullmatch(line):
            out.append(Problem(rel, "supportedLines", f"{shown(line)} is not a release line such as v1.103.x (the Gateway page looks lines up in that spelling)"))
    for release in _records(doc, "releases"):
        version = release.get("version")
        if isinstance(version, str) and not _RELEASE.fullmatch(version):
            out.append(Problem(rel, "releases", f"version {shown(version)} is not a release version such as v1.102.1"))
    for entry in _records(doc, "avoid"):
        spec = entry.get("versions")
        if not isinstance(spec, str):
            continue
        parts = [part.strip() for part in re.split("[、,]", spec) if part.strip()]
        if not parts:
            out.append(Problem(rel, "avoid", f"versions {shown(spec)} names no version"))
        for part in parts:
            found = _avoid_part_problem(part)
            if found:
                out.append(Problem(rel, "avoid", f"versions {shown(spec)}: {shown(part)} {found[0]}", found[1]))
    return out


def validate_docs(schema: Any, docs: dict[str, Any]) -> list[Problem]:
    """Schema-validate every known file and run the cross-file checks.  Empty list == valid."""
    problems: list[Problem] = []
    known = [rel for rel in sorted(docs) if definition_for(rel) is not None]
    for rel in known:
        problems.extend(validate_definition(schema, definition_for(rel) or "", docs[rel], rel))
    for rel in known:
        problems.extend(Problem(rel, where, "is not a finite number (NaN / Infinity cannot be stored as JSON)") for where in non_finite(docs[rel]))
        problems.extend(Problem(rel, where, f"is larger than any number a browser reads exactly ({MAX_NUMBER})") for where in too_large(docs[rel]))
        problems.extend(link_problems(rel, docs[rel]))
        problems.extend(text_problems(rel, docs[rel]))
        problems.extend(duplicate_records(rel, docs[rel]))

    # The schema pass above reports wrong shapes; the cross-file checks below only look at what has the right shape,
    # so a damaged document yields problems instead of an exception.
    sources = _records(docs.get("data/sources.json"), "sources")
    source_ids = {s["id"] for s in sources if isinstance(s.get("id"), str)}
    for rel in known:
        if rel == "data/sources.json":
            continue
        for sid, where in iter_source_refs(docs[rel]):
            if not isinstance(sid, str):
                problems.append(Problem(rel, where, f"source id must be a string, got {json.dumps(sid, ensure_ascii=True)[:80]}"))
            elif sid not in source_ids:
                problems.append(Problem(rel, where, f"unknown source id '{sid}'"))
        problems.extend(_bad_dates(rel, docs[rel]))

    # dates inside sources.json (the portal tool skips the file for source-ref checks only)
    if "data/sources.json" in docs:
        problems.extend(_bad_dates("data/sources.json", docs["data/sources.json"]))
        _duplicates(problems, "data/sources.json", "source", [s.get("id") for s in sources])

    if docs.get("data/catalog.json"):
        models = _records(docs["data/catalog.json"], "models")
        mids = [m.get("id") for m in models]
        known_ids = {m for m in mids if isinstance(m, str)}
        _duplicates(problems, "data/catalog.json", "model", mids)
        _duplicates(problems, "data/catalog.json", "offering", [o.get("id") for m in models for o in _records(m, "offerings")])
        for rel in known:
            model_id = docs[rel].get("modelId") if isinstance(docs[rel], dict) else None
            if rel.startswith("data/models/") and (not isinstance(model_id, str) or model_id not in known_ids):
                shown = f"'{model_id}'" if isinstance(model_id, str) else json.dumps(model_id, ensure_ascii=True)[:80]
                problems.append(Problem(rel, "", f"modelId {shown} not in catalog"))
    if docs.get("data/lifecycle.json"):
        _duplicates(problems, "data/lifecycle.json", "event", [e.get("id") for e in _records(docs["data/lifecycle.json"], "events")])
    if "data/litellm.json" in docs:
        problems.extend(gateway_problems("data/litellm.json", docs["data/litellm.json"]))
    return problems


def new_problems(before: Iterable[Problem], after: Iterable[Problem]) -> list[Problem]:
    """Problems that exist after a change but did not before it (pre-existing ones are not blamed).

    Counted, not just looked up: a second copy of a problem that was there once is a new problem.
    """
    left = Counter((p.file, p.where, p.message) for p in before)
    out = []
    for p in after:
        key = (p.file, p.where, p.message)
        if left[key] > 0:
            left[key] -= 1
        else:
            out.append(p)
    return out


def shorten(problems: list[Any], limit: int = 20) -> list[str]:
    """For display only: the first ``limit`` problems and how many more there are."""
    shown = [str(p) for p in problems[:limit]]
    if len(problems) > limit:
        shown.append(f"… {len(problems) - limit} more")
    return shown
