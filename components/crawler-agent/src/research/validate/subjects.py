"""Is this quote about that model?

A lifecycle table has a row per model and the rows look alike; so do the names: "Claude Sonnet 5" and "Claude Sonnet
5.5", "Claude 3 Haiku" and "Claude 3.5 Haiku", "o3" and "o3-mini".  A proposal may only use a quote for a record when
the quote names that record's model - the whole name, not a piece of it - and when the value it takes from the quote
stands with that name, not on the next row and not behind the name of another model on the same row.

Names are compared as names: case, spaces, hyphens and the "." / "-" inside a version are spelling
("claude-sonnet-5-5" is "Claude Sonnet 5.5"); a longer number or one more word of the kind model names end in is another
model.

The answer has three values, not two (``judge``):

``yes``     the row / sentence that holds the value is about the subject, in one of the few shapes a program can be
            sure of: the subject's name is the only name in front of the value (or shares it: "A and B retire on ...");
            the row begins with the value and the subject is the first name behind it; the unit names nobody and the
            heading it stands under names the subject alone.
``no``      it is about something else: another model's row, the subject only named as the model to move to, nobody
            named anywhere.
``unsure``  everything in between - two models in front of one value ("migrate from A to B before ..."), a name
            this code has never seen ("Command R+", "Stable Diffusion XL"), the subject's name going on ("GPT-4o Audio
            Preview"), a date with two readings.  The proposal may be made; it is flagged and a person looks.

This is a heuristic.  It cannot know every vendor's naming or every layout, and it does not know which column of a
row a number stands in.  What it does guarantee is the direction of its mistakes: what it cannot place is ``unsure``,
and nothing that is ``unsure`` is published without a person.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from ..portal.labels import version_backed, version_spans, versions_in
from . import dates as D
from .grounding import normalize, normalize_with_map
from .units import cells_of, units_of
from .words import COMMON, PLATFORMS, TECH

#: fields of a record that say which model (or deployment of it) the record is about
NAME_KEYS = ("modelName", "name", "providerModelId", "vendorModelId", "modelId")
#: Words that make a longer name of a model's name when they follow it: "<name> mini" is another model than "<name>".
#: Only words that are hardly anything else in that place - "Claude Sonnet 5.5 code execution" and "... max effort" are
#: sentences about Claude Sonnet 5.5.  (Joined with a hyphen, any word continues the name: see _ID_AFTER.)
VARIANT_WORDS = frozenset((
    "mini nano micro pro plus ultra lite flash turbo instant preview exp experimental haiku sonnet opus premier sonic canvas reel sol luna "
    "astra omni codex instruct embed embedding embeddings scout maverick"
).split())
#: what may follow a family word to make it somebody's model or product: "Mistral Large", "Claude Code", "GPT Image".
#: Written with a capital letter right behind a model's own name, such a word carries the name on ("GPT-4o Audio
#: Preview" is not GPT-4o) - in lower case it is the sentence going on ("Claude Sonnet 5.5 code execution").
_FOLLOWERS = VARIANT_WORDS | frozenset((
    "small medium large tiny xl max fast high low light latest thinking reasoning vision audio image video realtime code coder chat deep "
    "legacy classic beta alpha guard text multimodal live native search transcribe tts speech voice music robotics computer moderation rerank"
).split())
#: the words among them that say a size or a stage and nothing else: "Large 3" on its own is not a name
_GENERIC_WORDS = frozenset("mini nano micro tiny small medium large xl pro plus max ultra lite light turbo fast instant preview exp experimental beta alpha latest high low deep".split())
#: the first word of model names the portal may not know yet: "<word> 4" or "<word> pro" on a line is somebody's model
FAMILY_WORDS = frozenset((
    "claude gpt chatgpt gemini gemma nova titan llama mistral mixtral codestral pixtral ministral magistral devstral phi grok deepseek qwen "
    "kimi glm ernie doubao hunyuan sonar jurassic jamba palmyra granite whisper sora veo imagen lyria davinci codex marengo pegasus"
).split())
#: Behind a family word these name a product, a plan or the family itself - not a model: "Claude Code", "Claude Pro and
#: Max" (plans), "the Gemini API".  ("*": behind any family word.)
_NOT_MODELS = {
    "*": frozenset("api apis sdk sdks app apps family series docs documentation developer developers platform console team teams enterprise".split()),
    "claude": frozenset("code pro max ai desktop cowork design science security chrome".split()),
    "gemini": frozenset("advanced cli code enterprise".split()),
    "chatgpt": frozenset("plus pro go edu atlas".split()),
    "gpt": frozenset("store builder".split()),
}
#: "the Claude 5.5 family", "the GPT-6 series": the family, not a model called "Claude 5"
_FAMILY_NOUN = re.compile(r"(?:[.\-]\d+)*\s+(?:model\s+)?(?:family|families|series|line|lineup|generation|models)\b")
_PARENS = re.compile(r"\([^)]*\)|（[^）]*）")
#: one field naming several models ("gemini-2.5-pro, gemini-2.5-flash", "Pro / Flash")
_LIST = re.compile(r"\s*[,、，;；]\s*|\s+/\s+|\s+(?:and|及|與|和)\s+")
_PART = re.compile(r"[a-z]+|[0-9]+")
#: The id of a security advisory in a public database: CVE-2026-12345, GHSA-xxxx-xxxx-xxxx, PYSEC-2026-77, OSV / distribution ids.
#: Such an id is looked for on the page as it is written - whole: CVE-2026-1234 is not in "CVE-2026-12345".  (An id the
#: team made up for an incident without one - "LITELLM-INCIDENT-..." - is nothing a page could be searched for.)
_ADVISORY_ID = r"(?:cve-\d{4}-\d{4,}|ghsa(?:-[a-z0-9]{4}){3}|(?:pysec|rustsec|osv|go|mal|gms|zdi|euvd|jvndb|cnvd|cnnvd|usn|dsa|dla|rhsa|alas|msrc)-\d{4}-\d{1,8})"
_EXTERNAL_ID = re.compile(r"(?i)" + _ADVISORY_ID)
_ANY_EXTERNAL = re.compile(r"(?<![a-z0-9-])" + _ADVISORY_ID + r"(?![a-z0-9]|-[a-z0-9])")
#: a vendor's name in front of a model name is left out as often as not ("Nova Premier" for "Amazon Nova Premier")
VENDOR_WORDS = frozenset("amazon anthropic openai google meta microsoft mistral cohere deepseek moonshot aws azure xai nvidia alibaba".split())
# after a name: not one more number of a version ("5" is not "5.5"; an 8-digit date in a model id is not a version), and
# not "v2" (the second version of a model is another model; "-v1:0" is how a provider id ends)
# (a colon goes on with a name as in "...-v1:0" - not in "Claude 3 Haiku：09-12 EOL", where a sentence goes on with a day)
_VERSION_AFTER = r"(?![.\-_]\d{1,3}(?![0-9]))(?!:\d{1,3}(?![0-9\-/.年月]))(?![\s_-]{1,2}v(?:[2-9]|[1-9]\d))"
# ... and not one more word joined on with a hyphen: "gpt-4o-mini", "o3-deep-research" and "grok-4-fast" are identifiers of
# their own ("-v1" is how a provider id goes on)
_ID_AFTER = r"(?![-_](?!v\d)[a-z])"
#: a word and what follows it: "<family> 3", "<family>-pro", "<family>-r1" (the follower is looked at, not consumed)
_WORD_THEN = re.compile(r"(?<![a-z0-9])([a-z]{3,})(?=[\s\-_.]{0,2}([a-z]+[0-9]*|[0-9]+))")
_CODE = re.compile(r"[a-z]{1,2}[0-9]+")  # r1, k3, x5 behind a family word
_O_SERIES = re.compile(r"(?<![a-z0-9])o[1-9](?![0-9a-z])")  # o1, o3, o4-mini: a family whose first "word" is one letter
#: words that introduce the model to move to.  What a line says before them is about the model the page is talking
#: about, not about the one named after them ("Retires on 2026-09-12. Recommended replacement: Claude Haiku 4.5").
_CUE = re.compile(r"replac\w*|migrat\w*|upgrad\w*|successor|supersed\w*|instead|alternative|(?:switch|mov|transition)\w*\s+to|"
                  r"改用|替代|取代|遷移|迁移|升級|升级|後繼|后继|移行|後継|代替")
#: the heading of a column that names the model to move to
_POINTER_HEAD = re.compile(r"replac\w*|migrat\w*|upgrad\w*|successor|supersed\w*|alternative|recommend\w*|suggest\w*|"
                           r"改用|替代|取代|遷移|迁移|升級|升级|後繼|后继|建議|建议|推薦|推荐|移行|後継|代替")
#: between two names that share what the sentence says: "A and B will retire on ...", "A, B, and C"
_JOINED = re.compile(r"\s*(?:,|、|，|;|/|&|\+)?\s*(?:and|or|及|與|和|以及|或)?\s*")
#: "A costs the same as B: $3" - what follows is A's (and B's)
_SAME = re.compile(r"same\b|identical|equal\s+to|as\s+with|as\s+for\b|likewise|相同|一樣|一样|同樣|同样|等同")


def parts_of(name: str) -> tuple[str, ...]:
    """The words and numbers of a name, in order ("Claude Sonnet 5.5" and "claude-sonnet-5-5": claude, sonnet, 5, 5)."""
    return tuple(_PART.findall(normalize(_PARENS.sub(" ", name))))


def _usable(parts: tuple[str, ...]) -> bool:
    return bool(parts) and (len(parts) >= 2 or (parts[0].isalpha() and len(parts[0]) >= 4))


def _between(a: str, b: str) -> str:
    if a.isdigit() and b.isdigit():
        return r"[.\-_:]"  # inside a version: 5.5, 5-5
    if a.isalpha() and b.isalpha():
        return r"[\s\-_.:/]{1,3}"
    return r"[\s\-_.]{0,2}"  # gpt-6, GPT 6, gpt6; 4.5-haiku


def _pattern(parts: tuple[str, ...], own_words: frozenset[str], product: bool = False) -> re.Pattern:
    body = re.escape(parts[0])
    for a, b in zip(parts, parts[1:]):
        body += _between(a, b) + re.escape(b)
    if product:
        # a piece of software is named with its version behind it ("litellm-1.104.0", "litellm:1.104.0", "litellm==1.104.0");
        # one more word joined on is another package ("litellm-proxy-extras 0.2.1")
        return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9+])" + _ID_AFTER)
    variants = sorted(VARIANT_WORDS - own_words)
    after = r"(?![a-z0-9+])" + _VERSION_AFTER + _ID_AFTER  # ("Command R+" is not "Command R")
    if variants:
        after += r"(?![\s_-]{1,2}(?:" + "|".join(variants) + r")(?![a-z]))"
    return re.compile(r"(?<![a-z0-9])" + body + after)


@dataclass(frozen=True)
class Subject:
    """What a record is about: every spelling it may be named by."""

    names: tuple[str, ...]  # as written in the record (for messages)
    forms: tuple[tuple[str, ...], ...] = field(compare=False, default=())  # the parts of each usable name (and its short form)
    patterns: tuple[re.Pattern, ...] = field(compare=False, default=(), repr=False)
    external: tuple[str, ...] = ()  # advisory ids (CVE-..., GHSA-...): matched as written
    versions: tuple[str, ...] = ()  # a software release is "about" its version (LiteLLM v1.104.0)
    #: "product": a piece of software the facts are about (LiteLLM) - lines about it name models freely, and what is
    #: "another name" there is another piece of software ("Requires Python 3.9", "Boto3 1.40.0")
    product: bool = False

    def __bool__(self) -> bool:
        return bool(self.patterns or self.external or self.versions)

    @property
    def kind(self) -> str:
        return "product" if self.product and self.forms else "model" if self.forms else "advisory" if self.external else "release" if self.versions else "none"

    @property
    def shown(self) -> str:
        return " / ".join(dict.fromkeys([*self.names, *self.external, *(f"v{v}" for v in self.versions)])) or "the subject"


def subject_from(names: Iterable[str], external: Iterable[str] = (), product: bool = False) -> Subject:
    forms: list[tuple[str, ...]] = []
    kept: list[str] = []
    spelled: list[tuple[str, tuple[str, ...]]] = []
    loose: list[tuple[str, tuple[str, ...]]] = []
    for name in names:
        if not isinstance(name, str) or not 2 <= len(name) <= 200:
            continue
        pieces = [x for x in _LIST.split(_PARENS.sub(" ", name.split("@")[0])) if x.strip()]
        if len(pieces) > 1:
            # a list of models: each entry that is a name of its own counts - a word and a number at least
            # ("flash-lite" out of "gemini-2.5-pro / flash / flash-lite" and "v1:1" out of "…-v1:0, v1:1" are not)
            for piece in pieces:
                parts = parts_of(piece)
                if any(x.isdigit() for x in parts) and any(x.isalpha() and len(x) >= 3 for x in parts):
                    spelled.append((piece.strip(), parts))
                else:
                    loose.append((piece.strip(), parts))
        else:
            spelled.append((name, parts_of(name.split("@")[0])))
    if not any(_usable(parts) for _name, parts in spelled):
        spelled = loose  # "whisper / tts / tts-hd": no entry has a number, so the entries themselves have to do
    for name, parts in spelled:
        if not _usable(parts):
            continue
        kept.append(name)
        if parts not in forms:
            forms.append(parts)
        # Two habits of leaving the first word out: Anthropic's own pages say "Sonnet 5.5" for "Claude Sonnet 5.5", and
        # everybody drops the vendor ("Nova Premier" for "Amazon Nova Premier").  Nothing else is shortened: "Image 2.5"
        # is not a name for "MAI-Image-2.5" that another vendor's model could not answer to as well - and what is left
        # must still be a name of two parts ("DeepSeek Chat" is not known as "chat").
        short = parts[1:]
        family = parts[0] == "claude" and len(short) >= 2 and short[0].isalpha() and any(x.isdigit() for x in short)
        unbranded = parts[0] in VENDOR_WORDS and len(short) >= 2 and short[0].isalpha() and short[0] not in _GENERIC_WORDS
        if (family or unbranded) and short not in forms:
            forms.append(short)
    own = frozenset(p for form in forms for p in form)
    return Subject(tuple(kept), tuple(forms), tuple(_pattern(form, own, product) for form in forms),
                   tuple(e for e in external if isinstance(e, str) and _EXTERNAL_ID.fullmatch(e)), product=product)


def subject_of(record: Any) -> Subject:
    """The subject a record names itself (an empty one when it names none): a model by its names, an advisory by its
    CVE / GHSA id, a software release (an object with ``version`` and ``channel`` or ``supported``) by its version."""
    if not isinstance(record, dict):
        return Subject(())
    ident = record.get("id")
    external = [ident] if isinstance(ident, str) and _EXTERNAL_ID.fullmatch(ident) else []
    subject = subject_from([record.get(k) for k in NAME_KEYS if isinstance(record.get(k), str)], external)
    if not subject and ("channel" in record or "supported" in record):
        release = versions_in(record.get("version"))[:1]
        if release:
            return Subject((), versions=tuple(release))
    return subject


def _within(short: tuple, long: tuple) -> bool:
    return any(long[i:i + len(short)] == short for i in range(len(long) - len(short) + 1))


def names_agree(a: str, b: str) -> bool:
    """Do two names of one record name the same model?  ("Claude 3 Haiku" and "anthropic.claude-3-haiku-20240307-v1:0"
    do: the parts of one are found, in order and together, in the other.  A name that is no usable name agrees with
    anything - there is nothing in it to disagree with.)"""
    x, y = subject_from([a]).forms, subject_from([b]).forms
    if not x or not y:
        return True
    return any(_within(m, n) or _within(n, m) for m in x for n in y)


# ---------------------------------------------------------------------------------------------
# where on a line a model is named
# ---------------------------------------------------------------------------------------------
Span = tuple[int, int]


def _own_spans(subject: Subject, hay: str) -> list[Span]:
    return sorted({m.span() for p in subject.patterns for m in p.finditer(hay)})


#: what a name that began with a family word goes on with: the rest of its number, the words model names end in
_NAME_TAIL = re.compile(r"(?:[.\-_:]\d+)*(?:[\s\-_]{1,2}(?:" + "|".join(sorted(_FOLLOWERS, key=len, reverse=True)) + r")(?![a-z0-9]))*(?:[.\-_:]\d+)*")


#: "Claude Opus 4.5", "Mistral Large 3": the small number behind a name that ends in a word is the rest of the name -
#: also in front of a month ("Claude Opus 4 June 2027" gives a month, not the fourth of June).  Not the first figure of
#: a longer number or of a date written in figures, and not a quantity ("Nova Pro 2 years ago", "Claude Haiku 3 times").
_NUMBER_BEHIND = re.compile(r"[ \t]\d{1,2}(?:\.\d{1,2})?(?![\d.,:/\-]*\d)(?![ \t]?(?:%|[km]\b|tokens?\b|days?\b|weeks?\b|months?\b|years?\b|hours?\b|"
                            r"minutes?\b|seconds?\b|times\b|x\b|million\b|billion\b|thousand\b|usd\b|美元|元|萬|万|億|亿|個|个|天|週|周|年|倍))")


def _merge(spans: Iterable[Span]) -> list[Span]:
    """Overlapping spans as one (a name is found by several rules at once: "claude sonnet" and "sonnet 4.5")."""
    out: list[Span] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _family_spans(hay: str, leads: frozenset | set) -> set[Span]:
    """Where ``hay`` names a model by a family word and what follows it: "<family> 3", "<family> Large", "<family>-r1"."""
    found: set[Span] = set()
    for m in _WORD_THEN.finditer(hay):
        lead, follower = m.group(1), m.group(2)
        if lead not in leads:
            continue
        word = follower.rstrip("0123456789") or follower
        if not (follower.isdigit() or word in _FOLLOWERS or _CODE.fullmatch(follower)):
            continue
        if follower in _NOT_MODELS["*"] or follower in _NOT_MODELS.get(lead, ()):
            continue  # "Claude Code", "the Gemini API"
        if follower.isdigit() and _FAMILY_NOUN.match(hay, m.end(2)):
            continue  # "the Claude 5.5 family"
        end = _NAME_TAIL.match(hay, m.end(2)).end()
        number = _NUMBER_BEHIND.match(hay, end) if hay[end - 1:end].isalpha() else None
        found.add((m.start(), number.end() if number else end))
    return found


@lru_cache(maxsize=20_000)
def _marks(subject: Subject, hay: str, known: tuple) -> tuple[tuple[Span, ...], tuple[Span, ...]]:
    """(where ``hay`` names ``subject``, where it names another model).

    Another model: one the portal knows by another name; or a family word - the subject's own first word, or one of
    FAMILY_WORDS - followed by a number or by a word model names are built from ("Claude 3.5 Haiku" on a line read for
    "Claude 3 Haiku", "Mistral Large 3" on a line read for anything).  What overlaps the subject's own name is its name.
    """
    mine = _own_spans(subject, hay)
    if not subject.forms:
        return tuple(mine), ()  # a release or an advisory: its lines name the models it is about, and that is fine
    own = set(subject.forms)
    found: set[Span] = set()
    for other in known:
        if other.forms and not (set(other.forms) & own):
            found.update(m.span() for p in other.patterns for m in p.finditer(hay))
    # a name the portal has for another model, and the subject's name is only the beginning of it ("Grok 4" in "Grok 4 Fast")
    mine = [s for s in mine if not any(o[0] <= s[0] and s[1] <= o[1] and o[1] - o[0] > s[1] - s[0] for o in found)]
    leads = {form[0] for form in subject.forms if form[0].isalpha() and len(form[0]) >= 3} | FAMILY_WORDS
    found |= _family_spans(hay, leads)
    found.update(m.span() for m in _O_SERIES.finditer(hay))
    others = _merge(s for s in found if not any(s[0] < end and start < s[1] for start, end in mine))
    return tuple(_merge(mine)), tuple(others)


@lru_cache(maxsize=20_000)
def _models(hay: str, known: tuple) -> tuple[Span, ...]:
    """Where ``hay`` names any model: one the portal knows, or one of a known family."""
    found: set[Span] = set()
    for other in known:
        if other.forms and not other.product:
            found.update(m.span() for p in other.patterns for m in p.finditer(hay))
    found |= _family_spans(hay, FAMILY_WORDS)
    found.update(m.span() for m in _O_SERIES.finditer(hay))
    return tuple(_merge(found))


def named_models(text: str, known: Iterable[Subject] = ()) -> list[Subject]:
    """The models ``text`` names: the ones the portal knows (as it knows them), and - by the name the text gives them -
    the ones it does not ("Claude 3.5 Haiku" in a digest entry about a model that is no record yet)."""
    known = tuple(known)
    hay = normalize(text)
    out: list[Subject] = [subject for subject in known if subject.forms and not subject.product and mentions(subject, hay, normalized=True)]
    forms = {form for subject in out for form in subject.forms}
    for start, end in _models(hay, known):
        subject = subject_from([hay[start:end]])
        if subject and not (set(subject.forms) & forms) and not any(set(subject.forms) & set(k.forms) for k in known):
            forms.update(subject.forms)
            out.append(subject)
    return out


def without_names(text: str, known: Iterable[Subject] = ()) -> str:
    """``text``, folded, with every model name blanked (the portal's models and the models of known families): what
    dates and amounts are read from, so that the "5" of "GPT-5 March 2027" is not a day and "4.5" not a price."""
    hay = normalize(text)
    return _blanked(hay, _models(hay, tuple(known)))


def mentions(subject: Subject, text: str, *, normalized: bool = False) -> bool:
    """Is ``subject`` named in ``text`` - its whole name, not the beginning of a longer one?"""
    if not subject or not text:
        return False
    hay = text if normalized else normalize(text)
    if any(p.search(hay) for p in subject.patterns):
        return True
    if subject.external and {m.group(0) for m in _ANY_EXTERNAL.finditer(hay)} & {e.lower() for e in subject.external}:
        return True  # (an id is found whole: CVE-2026-1234 is not in "CVE-2026-12345")
    return bool(subject.versions) and any(version_backed(v, versions_in(hay)) for v in subject.versions)


def names_another(subject: Subject, text: str, known: Iterable[Subject] = ()) -> bool:
    """``text`` does not name ``subject`` but does name another model: one the portal knows, or one of a known family."""
    hay = normalize(text)
    if mentions(subject, hay, normalized=True):
        return False
    return bool(_marks(subject, hay, tuple(known))[1])


# ---------------------------------------------------------------------------------------------
# names this code does not know
# ---------------------------------------------------------------------------------------------
_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+]*(?:[-_./:'’][A-Za-z0-9+]+)*")
#: an address or a file: its words are not names ("api.example.com/v1/messages", "litellm-1.104.0-py3-none-any.whl")
_ADDRESS = re.compile(r"(?i)(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?|\S+\.(?:html?|json|ya?ml|md|txt|csv|py|js|ts|whl|tar|gz|zip|pdf|png|jpe?g|svg)")
#: tokens with a digit in them that are no names: a version, a percentile, a quarter, a cloud region, an encoding ...
_NOT_A_NAME = re.compile(
    r"v\d[\w.:\-]*|p\d{2,3}|q[1-4]|h[12]|fy\d{2,4}|"
    r"(?:us|eu|ap|sa|ca|me|af|il|cn|mx)-(?:gov-)?[a-z]+-\d[a-z]?|[a-z]*(?:east|west|north|south|central)[a-z]*\d?|"
    r"utf-?(?:8|16|32)|sha-?\d+|md5|base64|x86(?:[_-]64)?|x64|arm64|amd64|ipv[46]|http/?[123](?:\.\d)?|oauth2?|k8s|s3|ec2|i18n|l10n|e2e|b2[bc]|24x7|"
    r"h26[45]|mp[34]|cve-\d{4}-\d+|ghsa(?:-[a-z0-9]{4}){3}|rfc-?\d+|iso-?\d+[\w\-]*|"
    r"(?:tier|level|phase|step|top|gen|type|class|version|option|part|day|week|month|year|round|wave|zone|group|stage|rev|build|issue|item|page|line|row|"
    r"fig|figure|table|section|chapter|appendix|note|footnote)[-_]?\d[\w.\-]*")


def _known_word(word: str, allowed: frozenset) -> bool:
    return len(word) <= 1 or word.isdigit() or word in allowed or word in FAMILY_WORDS or word in VENDOR_WORDS


def _is_odd(token: str, allowed: frozenset) -> bool:
    """Is ``token`` (as written on the page) something that may be a name this code does not know?

    An identifier with a figure in it ("gpt-oss-120b", "K3", "babbage-002"), or a word written with a capital letter
    that is neither a common word nor a vendor, a platform or a place ("Mythos", "Command", "DeepSeek-R1").  Words in
    lower case never are: that is how sentences are written.
    """
    word = re.sub(r"(?:n['’]t|['’](?:s|ve|re|ll|d|m))$", "", token.strip("."))  # We've, Anthropic's, Don't
    if len(word) <= 1:
        return False
    low = word.lower()
    if any(ch.isdigit() for ch in word):
        return _NOT_A_NAME.fullmatch(low) is None
    if not word[0].isupper() and not any(ch.isupper() for ch in word[1:]):
        return False
    parts = [p for p in re.split(r"[-_./:'’+]", low) if p]
    return any(not _known_word(p, allowed) for p in parts)


@dataclass(frozen=True)
class _Piece:
    """One cell of a row, one sentence, one heading - read for one subject."""

    text: str  # folded (grounding.normalize): what names and values are looked for in
    own: tuple[Span, ...]  # where it names the subject
    other: tuple[Span, ...]  # where it names something of the subject's kind that is not the subject
    odd: tuple[Span, ...]  # where it holds a name this code does not know
    blank: str  # ``text`` with every model name blanked: the figures of a name are not values
    loose: bool  # the subject's name goes on ("GPT-4o Audio Preview" read for GPT-4o)
    raw: str = ""

    @property
    def named(self) -> bool:
        return bool(self.own or self.other or self.odd)


_JOINT = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_.:@/")


def _blanked(hay: str, spans: Iterable[Span]) -> str:
    """``hay`` with the names at ``spans`` blanked - each together with the identifier it is part of: of
    "anthropic.claude-sonnet-5-5-20260928-v1:0" nothing is left, not a loose "20260928" and a "0" that read as amounts."""
    out = list(hay)
    for start, end in spans:
        while start > 0 and hay[start - 1] in _JOINT:
            start -= 1
        while end < len(hay) and hay[end] in _JOINT and (hay[end].isalnum() or (end + 1 < len(hay) and hay[end + 1].isalnum())):
            end += 1
        out[start:end] = " " * (end - start)
    return "".join(out)


#: Other software, on a line read for a product (LiteLLM): a tool, a language or a library with a figure right behind
#: it ("Python 3.9.2", "Boto3 version 1.40.0", "node 20"), a requirement ("orjson>=3.9.7", "openai==1.99.5",
#: "pydantic@2.5"), a file or a tag of something else ("redis-7.2.4").  That figure is the other thing's version, not a
#: value of the product - whatever the page is about.
_FIGURE = r"v?\d[\w.\-+]*"
_OTHER_SOFTWARE = re.compile(
    r"(?<![a-z0-9_.\-/])(?:"
    r"(?:" + "|".join(sorted((re.escape(w) for w in TECH), key=len, reverse=True)) + r")(?![a-z])(?:\s?\([a-z0-9 .\-]{1,30}\))?"
    r"[\s\-_/:]{0,2}(?:(?:versions?|release)\s+)?(?:[<>=~!^≥≤]{1,2}\s?)?" + _FIGURE +
    r"|[a-z][a-z0-9_.\-]*(?:[<>=~!^]{1,2}|[≥≤@])" + _FIGURE +
    r"|[a-z][a-z0-9_]*-\d+\.\d[\w.\-+]*"
    r")")
_NOT_SOFTWARE = frozenset("version versions release releases v ver tag".split())


@lru_cache(maxsize=64)
def _longer_names(subject: Subject) -> re.Pattern:
    bodies = []
    for form in subject.forms:
        body = re.escape(form[0])
        for a, b in zip(form, form[1:]):
            body += _between(a, b) + re.escape(b)
        bodies.append(body)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(bodies) + r")(?:[-_](?!v\d)[a-z][a-z0-9]*)+")


def _software_spans(hay: str, mine: Iterable[Span]) -> list[Span]:
    """Where ``hay`` names other software together with its version (see _OTHER_SOFTWARE) - not where it names the
    product itself (``mine``)."""
    own = list(mine)
    out = []
    for m in _OTHER_SOFTWARE.finditer(hay):
        start, end = m.span()
        word = re.match(r"[a-z][a-z0-9_]*", m.group(0))
        if (word and word.group(0) in _NOT_SOFTWARE) or any(s < end and start < e for s, e in own):
            continue
        out.append((start, end))
    return out


#: the word a tag begins with in front of a full version: "main-v1.104.0-stable" (an image tag) names v1.104.0-stable
_TAG_PREFIX = re.compile(r"(?<![a-z0-9.\-_])[a-z]{2,12}[-_](?=v\d+\.\d+\.\d)")
#: what a file name goes on with behind the version: "litellm-1.104.0.tar.gz", "litellm-1.104.0-py3-none-any.whl"
_FILE_TAIL = re.compile(r"(?<=\d)(?:\.tar\.gz|\.tgz|\.zip|\.whl|-(?:py|cp)[23][\w.\-]*\.whl)(?![a-z0-9])")


def versions_at(text: str) -> list[tuple[int, int, str]]:
    """(start, end, version) of the software versions in ``text`` - not the figures of its dates, its amounts of money
    and its names and identifiers.  A version is read whole, with every tag that is joined on ("v1.74.9-stable.patch.1"):
    where it begins is decided on the text without identifiers, how far it goes on the text as it is."""
    def blank(m: re.Match) -> str:
        return " " * len(m.group(0))
    text = _FILE_TAIL.sub(blank, _TAG_PREFIX.sub(blank, re.sub(r"\$\s?\d[\d,.]*", blank, text)))
    kept = {start for start, _end, _version in version_spans(D.strip_non_quantities(text))}
    return [(start, end, version) for start, end, version in version_spans(text) if start in kept]


def _release_spans(subject: Subject, hay: str) -> tuple[list[Span], list[Span]]:
    """(where ``hay`` names the subject's release, where it names another one) - not counting the figures of dates,
    amounts and model names."""
    mine, others = [], []
    for start, end, version in versions_at(hay):
        (mine if any(version_backed(v, [version]) for v in subject.versions) else others).append((start, end))
    return mine, others


@lru_cache(maxsize=40_000)
def _piece(subject: Subject, raw: str, known: tuple) -> _Piece:
    norm = normalize_with_map(raw)
    hay = norm.text
    kind = subject.kind
    models = _models(hay, known)
    if kind == "model":
        mine, others = _marks(subject, hay, known)
        covered = [*mine, *others]
    elif kind == "product":
        mine = tuple(_own_spans(subject, hay))
        others = tuple(_software_spans(hay, mine))
        covered = [*models, *others]
    elif kind == "release":
        own, rest = _release_spans(subject, _blanked(hay, models))
        mine, others, covered = tuple(own), tuple(rest), list(models)
    elif kind == "advisory":
        wanted = {e.lower() for e in subject.external}
        ids = [(m.span(), m.group(0)) for m in _ANY_EXTERNAL.finditer(hay)]
        mine = tuple(span for span, text in ids if text in wanted)
        others = tuple(span for span, text in ids if text not in wanted)
        covered = list(models)
    else:
        mine, others, covered = (), (), list(models)
    odd: list[Span] = []
    loose = False
    if kind in ("model", "product"):
        allowed = COMMON | PLATFORMS | (TECH if kind == "model" else frozenset())
        for family in FAMILY_WORDS & set(re.findall(r"[a-z]+", hay)):  # "Claude Pro and Max subscribers": plans, not models
            allowed = allowed | _NOT_MODELS["*"] | _NOT_MODELS.get(family, frozenset())
        index = norm.index
        taken = sorted([*mine, *others, *covered])
        if kind == "product":
            # the product's name with one more word joined on is a package of its own, with versions of its own
            # ("litellm-proxy-extras 0.2.1") - or the product by another name ("litellm-database:main-v1.104.0"): who knows
            odd.extend(m.span() for m in _longer_names(subject).finditer(hay))
        for m in _TOKEN.finditer(raw):
            start = bisect.bisect_left(index, m.start())
            end = bisect.bisect_left(index, m.end())
            if start < end and hay[start] == " ":  # (the blank in front of a word stands at the word's own offset)
                start += 1
            if start >= end or any(s < end and start < e for s, e in taken):
                continue
            if raw[max(0, m.start() - 3):m.start()] == "://" or ("/" in m.group(0) and _ADDRESS.fullmatch(m.group(0))):
                continue
            if _is_odd(m.group(0), allowed):
                odd.append((start, end))
        for _start, end in mine:
            after = raw[index[end - 1] + 1:] if end else raw
            word = re.match(r"[ \t]?([A-Z][A-Za-z0-9+]*)", after)
            if word and (word.group(1).lower() in _FOLLOWERS or _is_odd(word.group(1), allowed)):
                loose = True
    blank = _blanked(hay, covered if kind != "model" else [*mine, *others])
    if kind == "product":  # its own name is blanked as it stands: "litellm-1.104.0" and "litellm:1.104.0" name a release of it
        blank = "".join(" " if any(s <= i < e for s, e in mine) else ch for i, ch in enumerate(blank))
    return _Piece(hay, tuple(mine), tuple(others), tuple(sorted(set(odd))), blank, loose, raw)


# ---------------------------------------------------------------------------------------------
# whose is the value?
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Found:
    """A value in a piece of text (offsets in the text that was searched)."""

    start: int
    end: int
    wanted: bool  # is it the value that is looked for?
    doubt: str = ""  # why this reading of it is not certain ("11/10/2026 can be read as ...")


#: (folded text with the model names blanked, the heading of its column or "") -> every value of one kind in it
Finder = Callable[[str, str], list[Found]]

YES, UNSURE, NO, ABSENT = "yes", "unsure", "no", "absent"
_RANK = {ABSENT: 0, NO: 1, UNSURE: 2, YES: 3}


@dataclass(frozen=True)
class Verdict:
    status: str  # yes | unsure | no | absent (the value is not in the quote)
    why: str = ""  # for unsure / no: what stands in the way

    @property
    def ok(self) -> bool:
        return self.status == YES


def _best(a: Verdict, b: Verdict) -> Verdict:
    return b if _RANK[b.status] > _RANK[a.status] else a


def _shown(piece: _Piece, spans: Iterable[Span], limit: int = 3) -> str:
    seen = list(dict.fromkeys(piece.text[s:e].strip() for s, e in spans))
    return ", ".join(repr(x) for x in seen[:limit]) + (", ..." if len(seen) > limit else "")


def _chain(piece: _Piece, names: list[tuple[int, int, str]]) -> bool:
    """Do the names (in order) share what follows them: joined like a list ("A, B and C"), or said to be the same?"""
    for (_s, end, _k), (start, _e, _k2) in zip(names, names[1:]):
        between = piece.text[end:start]
        if _JOINED.fullmatch(between) is None and _SAME.search(between) is None:
            return False
    return True


def _naming(piece: _Piece) -> str:
    """What a unit (or a heading above the quote) leaves the lines after it with: "own" (the subject alone), "other",
    "mixed" (the subject and something else, or its name going on), "odd" (only names nobody knows), "none"."""
    if piece.own:
        if piece.other:
            first = piece.own[0][0]
            # the subject leads and every other name is the model to move to: still the subject's line
            if first < piece.other[0][0] and all(_CUE.search(piece.text[first:start]) for start, _end in piece.other) and not piece.loose:
                return "own"
            return "mixed"
        return "mixed" if piece.odd or piece.loose else "own"
    if piece.other:
        return "other"
    return "odd" if piece.odd else "none"


class _Reading:
    """One quote read for one subject and one kind of value."""

    def __init__(self, subject: Subject, quote: str, context: str, find: Finder | None, known: tuple, heads: tuple, outer: int):
        self.subject, self.find, self.known, self.outer = subject, find, known, outer
        self.context = [line for line in context.split("\n") if line.strip()]
        self.units = units_of(quote, heads)
        self._whole: dict[int, _Piece] = {}
        self._values: dict[int, bool] = {}

    # -- the unit as one piece: who does it name, does it hold a value of the kind? -------------
    def whole(self, k: int) -> _Piece:
        if k not in self._whole:
            self._whole[k] = _piece(self.subject, self.units[k].text, self.known)
        return self._whole[k]

    def has_value(self, k: int) -> bool:
        if k not in self._values:
            unit = self.units[k]
            if self.find is None:
                self._values[k] = False
            elif unit.kind == "row":
                self._values[k] = any(self.find(piece.blank, head) for piece, head, _pointer in self.cells(k))
            else:
                self._values[k] = bool(self.find(self.whole(k).blank, ""))
        return self._values[k]

    def cells(self, k: int) -> list[tuple[_Piece, str, bool]]:
        """(the cell read for the subject, the heading of its column, is it a column that names the model to move to?)"""
        unit = self.units[k]
        spans = cells_of(unit.text)
        heads = [unit.head[a:b] for a, b in cells_of(unit.head)] if unit.head else []
        if len(heads) != len(spans):
            heads = [""] * len(spans)
        out = []
        for (a, b), head in zip(spans, heads):
            piece = _piece(self.subject, unit.text[a:b], self.known)
            pointer = bool(head) and _POINTER_HEAD.search(normalize(head)) is not None
            if not pointer and piece.named:
                first = min(s for s, _e in [*piece.own, *piece.other, *piece.odd])
                pointer = _CUE.search(piece.text[:first]) is not None  # "Replacement: X" in a cell
            out.append((piece, head, pointer))
        return out

    # -- a unit that names nobody: whose is it? -------------------------------------------------
    def above(self, upto: int | None = None) -> Verdict:
        """What the context of the quote says, innermost first: the heading (then the title, then the address) that
        names a model decides.  (``upto``: only the lines above that one.)"""
        for idx in range((len(self.context) if upto is None else upto) - 1, -1, -1):
            piece = _piece(self.subject, self.context[idx], self.known)
            naming = _naming(piece)
            if naming == "own":
                return Verdict(YES)
            if naming == "other":
                return Verdict(NO, f"the heading above it is about {_shown(piece, piece.other)}")
            if naming == "mixed":
                return Verdict(UNSURE, f"the heading above it ({self.context[idx][:80]!r}) names more than {self.subject.shown}")
            if naming == "odd" and idx >= self.outer:
                return Verdict(UNSURE, f"the heading above it ({self.context[idx][:80]!r}) may name something else: {_shown(piece, piece.odd)}")
        return Verdict(NO, "neither its line, nor the lines before it in the quote, nor the headings above the quote name a model")

    def dated_above(self) -> Verdict:
        """The value is not in the quote: is it in a heading the quote stands under?  A change log writes the day as a
        heading and the items of that day under it ("#### September 28, 2026" / "- We've launched ..."), release notes
        the release ("## v1.104.0-stable").  What stands under such a heading is of that day - and the subject's when
        the quote names the subject and nothing else.  The nearest heading that holds a value of the kind decides."""
        if self.find is None or not getattr(self.find, "in_headings", False):
            return Verdict(ABSENT)
        for idx in range(len(self.context) - 1, max(self.outer, 0) - 1, -1):
            heading = _piece(self.subject, self.context[idx], self.known)
            found = self.find(heading.blank, "")
            if not found:
                continue
            wanted = [f for f in found if f.wanted]
            if not wanted:
                return Verdict(ABSENT)
            verdict = self._under(idx, heading)
            doubt = next((f.doubt for f in wanted), "") if all(f.doubt for f in wanted) else ""
            return Verdict(UNSURE, doubt) if verdict.status == YES and doubt else verdict
        return Verdict(ABSENT)

    def _under(self, idx: int, heading: _Piece) -> Verdict:
        said = f"the value stands in the heading above the quote ({heading.raw[:60]!r})"
        pieces = [self.whole(k) for k in range(len(self.units))]
        if any(p.own for p in pieces):
            if any(p.own and p.loose for p in pieces):
                return Verdict(UNSURE, f"{said}, and the name goes on behind {self.subject.shown} in the quote: it may be another model's")
            rest = next((p for p in pieces if p.other or p.odd), None)
            if rest is not None:
                return Verdict(UNSURE, f"{said}, and the quote names {_shown(rest, [*rest.other, *rest.odd])} as well as {self.subject.shown}")
            return Verdict(YES)
        named = next((p for p in pieces if p.other), None)
        if named is not None:
            return Verdict(NO, f"{said}, and the quote under it is about {_shown(named, named.other)}")
        odd = next((p for p in pieces if p.odd), None)
        if odd is not None:
            return Verdict(UNSURE, f"{said}, and the quote under it holds a name this validator does not know ({_shown(odd, odd.odd)})")
        naming = _naming(heading)  # the quote names nobody: the heading itself, then what stands above it
        if naming == "own":
            return Verdict(YES)
        if naming == "other":
            return Verdict(NO, f"{said}, which is about {_shown(heading, heading.other)}")
        if naming != "none":
            return Verdict(UNSURE, f"{said}, which names more than {self.subject.shown}")
        return self.above(idx)

    def inherited(self, k: int) -> Verdict:
        unit = self.units[k]
        if unit.kind == "heading":  # a heading hands on to what stands under it ("## October 1, 2026" over the items of that day)
            for j in range(k + 1, len(self.units)):
                if self.units[j].kind == "heading":
                    break
                naming = _naming(self.whole(j))
                if naming == "none":
                    continue
                if naming == "own":
                    return Verdict(YES)
                if naming == "other":
                    return Verdict(NO, f"what stands under that heading is about {_shown(self.whole(j), self.whole(j).other)}")
                return Verdict(UNSURE, "what stands under that heading names more than one thing")
        for j in range(k - 1, -1, -1):
            piece = self.whole(j)
            naming = _naming(piece)
            if naming == "none":
                continue
            if naming == "own":
                if self.has_value(j) and self.units[j].kind != "heading":
                    return Verdict(UNSURE, f"its line names nobody, and the line before it that names {self.subject.shown} states a value of its own")
                return Verdict(YES)
            if naming == "other":
                return Verdict(NO, f"the line before it is about {_shown(piece, piece.other)}")
            return Verdict(UNSURE, f"the line before it names {_shown(piece, [*piece.other, *piece.odd]) or 'more than one thing'}")
        return self.above()

    # -- a sentence -----------------------------------------------------------------------------
    def prose(self, k: int) -> Verdict:
        piece = self.whole(k)
        found = sorted(self.find(piece.blank, ""), key=lambda f: (f.start, f.end)) if self.find else []
        names = sorted([(s, e, "own") for s, e in piece.own] + [(s, e, "other") for s, e in piece.other] + [(s, e, "odd") for s, e in piece.odd])
        best = Verdict(ABSENT)
        for t, value in enumerate(found):
            if value.wanted:
                verdict = self._sentence(k, piece, found, names, t)
                if verdict.status == YES and value.doubt:
                    verdict = Verdict(UNSURE, value.doubt)
                best = _best(best, verdict)
        return best

    def _sentence(self, k: int, piece: _Piece, found: list[Found], names: list, t: int) -> Verdict:
        value = found[t]
        if any(s < value.end and value.start < e for s, e in piece.own):
            return Verdict(YES)  # the value is the subject's own name (a release, stated by naming it)
        earlier = [f for f in found if f.end <= value.start]
        lo = max((f.end for f in earlier), default=0)
        inside = [n for n in names if lo <= n[0] < value.start]
        if inside:
            kinds = {kind for _s, _e, kind in inside}
            if "own" in kinds:
                if piece.loose:
                    return Verdict(UNSURE, f"the name goes on behind {self.subject.shown} ({piece.raw[:80]!r}): it may be another model's")
                if kinds == {"own"} or _chain(piece, inside):
                    return Verdict(YES)
                rest = [(s, e) for s, e, kind in inside if kind != "own"]
                return Verdict(UNSURE, f"the sentence names {_shown(piece, rest)} as well, in front of the value")
            if "other" in kinds:
                return Verdict(NO, f"the value stands behind {_shown(piece, [(s, e) for s, e, kind in inside if kind == 'other'])}")
            why = f"the value stands behind a name this validator does not know ({_shown(piece, [(s, e) for s, e, _k in inside])})"
            return Verdict(UNSURE, why) if piece.own else self._capped(k, why)
        if earlier:  # nothing named since the value before it: it is whose that one is
            return self._sentence(k, piece, found, names, max(i for i, f in enumerate(found) if f.end <= value.start and f.end == lo))
        hi = min((f.start for f in found if f.start >= value.end), default=len(piece.text))
        behind = [n for n in names if value.end <= n[0] < hi]
        if not behind:
            return self.inherited(k)
        start, end, kind = behind[0]
        cue = _CUE.search(piece.text[value.end:start]) is not None
        # the names right behind the value that are joined like a list share it: "On <date>, A and B will be retired"
        group = [behind[0]]
        for name in behind[1:]:
            if not _chain(piece, [group[-1], name]):
                break
            group.append(name)
        if any(k == "own" for _s, _e, k in group):
            kind = "own"
        if kind == "own":
            if cue:
                return Verdict(UNSURE, f"{self.subject.shown} is named there as the model to move to")
            return Verdict(UNSURE, "the name goes on behind it") if piece.loose else Verdict(YES)
        if kind == "other":
            return self.inherited(k) if cue else Verdict(NO, f"the value belongs to {_shown(piece, [(start, end)])}, named right behind it")
        return self._capped(k, f"the value stands in front of a name this validator does not know ({_shown(piece, [(start, end)])})")

    def _capped(self, k: int, why: str) -> Verdict:
        """A unit that holds a name nobody knows: at best unsure - and plainly not the subject's when what stands
        above it is about something else."""
        inherited = self.inherited(k)
        return inherited if inherited.status == NO else Verdict(UNSURE, why)

    # -- a table row ----------------------------------------------------------------------------
    def row(self, k: int) -> Verdict:
        cells = self.cells(k)
        best = Verdict(ABSENT)
        for ci, (piece, head, _pointer) in enumerate(cells):
            for value in self.find(piece.blank, head) if self.find else ():
                if value.wanted:
                    verdict = self._row(k, cells, ci, value)
                    if verdict.status == YES and value.doubt:
                        verdict = Verdict(UNSURE, value.doubt)
                    best = _best(best, verdict)
        return best

    def _row(self, k: int, cells: list, ci: int, value: Found) -> Verdict:
        subject = self.subject
        if any(s < value.end and value.start < e for s, e in cells[ci][0].own) and not cells[ci][2]:
            return Verdict(YES)  # the value is the subject's own name (a release, stated by naming it)
        own = [(i, s, e) for i, (p, _h, pointer) in enumerate(cells) if not pointer for s, e in p.own]
        pointed = any(p.own for p, _h, pointer in cells if pointer)
        others = [(i, s, e) for i, (p, _h, pointer) in enumerate(cells) if not pointer for s, e in p.other]
        odd = [(i, s, e) for i, (p, _h, pointer) in enumerate(cells) if not pointer for s, e in p.odd]

        def shown(found: list) -> str:
            return ", ".join(dict.fromkeys(repr(cells[i][0].text[s:e].strip()) for i, s, e in found[:3]))

        def before(name: tuple) -> bool:
            return name[0] < ci or (name[0] == ci and name[1] < value.start)

        if not own:
            if pointed:
                return Verdict(NO, f"the row names {subject.shown} only as the model to move to")
            if others:
                return Verdict(NO, f"the row is about {shown(others)}")
            if odd:
                return self._capped(k, f"the row holds a name this validator does not know ({shown(odd)})")
            return self.inherited(k)
        # another name in the subject's own cell, joined to it like a list ("A, B"), shares the row with it
        shared = set()
        for i in {i for i, _s, _e in own}:
            piece = cells[i][0]
            names = sorted([(s, e, "own") for s, e in piece.own] + [(s, e, "other") for s, e in piece.other] + [(s, e, "odd") for s, e in piece.odd])
            if len(names) > 1 and _chain(piece, names):
                shared |= {(i, s, e) for s, e, _kind in names}
        others_before = [n for n in others if before(n) and n not in shared]
        odd_before = [n for n in odd if before(n) and n not in shared and not any(n[0] == i for i, _s, _e in own)]
        if not any(before(n) for n in own):
            if others_before:
                return Verdict(NO, f"the value stands behind {shown(others_before)}, in front of {subject.shown}")
            if odd_before:
                return self._capped(k, f"the value stands behind a name this validator does not know ({shown(odd_before)})")
            # the row begins with the value: the first name behind it is the one it is about
            first = min([*own, *[n for n in others if n not in shared], *[n for n in odd if n not in shared and not any(n[0] == i for i, _s, _e in own)]])
            if first in others:
                return Verdict(NO, f"the row begins with the value and {shown([first])} is the first model named behind it")
            if first not in own:
                return self._capped(k, f"the row begins with the value and the first name behind it is one this validator does not know ({shown([first])})")
        else:
            if others_before:
                return Verdict(UNSURE, f"the row names {shown(others_before)} as well, in front of the value")
            if odd_before:
                return Verdict(UNSURE, f"the row holds a name this validator does not know in front of the value ({shown(odd_before)})")
        # the cell that names the subject must name it and nothing more ("GPT-4o Audio Preview" is not GPT-4o)
        for i in sorted({i for i, _s, _e in own}):
            piece = cells[i][0]
            if piece.loose or [n for n in piece.odd if (i, *n) not in shared]:
                return Verdict(UNSURE, f"the cell that names {subject.shown} says more than its name ({piece.raw[:80]!r}): it may be another model's row")
        return Verdict(YES)

    # -- the quote ------------------------------------------------------------------------------
    def verdict(self) -> Verdict:
        best = Verdict(ABSENT)
        for k, unit in enumerate(self.units):
            if not self.has_value(k):
                continue
            if unit.kind == "row":
                verdict = self.row(k)
            else:
                verdict = self.prose(k)
            best = _best(best, verdict)
            if best.status == YES:
                break
        return self.dated_above() if best.status == ABSENT else best

    def about(self) -> Verdict:
        """Is the quote about the subject at all (whatever it states)?"""
        pieces = [self.whole(k) for k in range(len(self.units))]
        if any(p.own and not p.loose for p in pieces):
            return Verdict(YES)
        if any(p.own for p in pieces):
            return Verdict(UNSURE, f"the name goes on behind {self.subject.shown}: it may be another model's")
        # (other software named on a line about a product - "Redis 7.2 is required" - does not make the line another product's)
        named = next((p for p in pieces if p.other), None) if not self.subject.product else None
        if named is not None:
            return Verdict(NO, f"the quote is about {_shown(named, named.other)}")
        above = self.above()
        odd = next((p for p in pieces if p.odd), None)
        if above.status == YES and odd is not None:
            return Verdict(UNSURE, f"the quote holds a name this validator does not know ({_shown(odd, odd.odd)})")
        return above


def judge(subject: Subject, quote: str, context: str = "", find: Finder | None = None, known: Iterable[Subject] = (),
          heads: tuple = (), outer: int = 0) -> Verdict:
    """Does ``quote`` state the value ``find`` looks for - for ``subject``?  (``find=None``: is it about ``subject``?)

    ``context``: what stands above the quote, outermost first and one per line (``evidence.context_for``); its first
    ``outer`` lines are the address and the page title, the rest are headings.  ``heads``: per line of the quote, the
    heading row of its table.  See the module's docstring for what the three answers mean.
    """
    if subject.product and not mentions(subject, f"{context}\n{quote}"):
        # (a model may be meant by a name this code does not know; a product is called what it is called)
        return Verdict(NO, f"neither the quote nor what stands above it (the headings, the page's title and address) names {subject.shown}")
    reading = _Reading(subject, quote, context, find, tuple(known), tuple(heads), outer)
    if not subject:
        if find is None:
            return Verdict(YES)
        doubt = ""
        for k, unit in enumerate(reading.units):
            pieces = [(p, h) for p, h, _pointer in reading.cells(k)] if unit.kind == "row" else [(reading.whole(k), "")]
            for piece, head in pieces:
                for value in find(piece.blank, head):
                    if value.wanted and not value.doubt:
                        return Verdict(YES)
                    if value.wanted:
                        doubt = value.doubt
        return Verdict(UNSURE, doubt) if doubt else Verdict(ABSENT)
    return reading.about() if find is None else reading.verdict()


def stands_with(subject: Subject, quote: str, context: str, find: Finder | None, known: Iterable[Subject] = (), heads: tuple = (), outer: int = 0) -> bool:
    """Does ``quote`` state the value for ``subject`` beyond doubt?  (``judge(...)`` says "yes".)"""
    return judge(subject, quote, context, find, known, heads, outer).status == YES


# ---------------------------------------------------------------------------------------------
# the usual values
# ---------------------------------------------------------------------------------------------
def date_finder(day) -> Finder:
    """The dates of a text, the one that is ``day`` marked as wanted."""
    def find(text: str, _head: str = "") -> list[Found]:
        return [Found(f.start, f.end, f.day == day, f.doubt) for f in D.date_spans(text)]
    find.in_headings = True  # a day may be the heading its items stand under (``_Reading.dated_above``)
    return find


def monthday_finder(month: int, day: int) -> Finder:
    """The days of a text, with or without their year; wanted: the ones that fall on ``month``/``day``."""
    def find(text: str, _head: str = "") -> list[Found]:
        full = [Found(f.start, f.end, (f.day.month, f.day.day) == (month, day), f.doubt) for f in D.date_spans(text)]
        return full + [Found(f.start, f.end, (f.month, f.day) == (month, day)) for f in D.partial_dates(text)]
    find.in_headings = True
    return find


def number_finder(value: float, per_1k: bool = False) -> Finder:
    """The amounts of a text; wanted: the one that is ``value`` - or, for a price (``per_1k``), a thousandth of it where
    the text prices 1,000 tokens."""
    def find(text: str, head: str = "") -> list[Found]:
        out = []
        for n in D.quantity_spans(text, (), head):
            try:
                exact = D.number_support(value, set(n.values)) == "exact"
                scaled = per_1k and n.per_1k and D.number_support(value, set(n.values), True) == "per-1k"
            except (OverflowError, ValueError):
                exact = scaled = False
            out.append(Found(n.start, n.end, exact or scaled))
        return out
    return find


def version_finder(version: str) -> Finder:
    """The software versions of a text - not the figures of its dates and amounts; wanted: one that states ``version``."""
    def find(text: str, _head: str = "") -> list[Found]:
        return [Found(start, end, version_backed(version, [found])) for start, end, found in versions_at(text)]
    find.in_headings = True  # ... and so may a release
    return find


def anything(_text: str, _head: str = "") -> list[Found]:
    return []


def portal_subjects(docs: dict[str, Any]) -> tuple[Subject, ...]:
    """Every model the portal names (catalog models and the models of lifecycle events)."""
    out: list[Subject] = []
    seen: set[tuple] = set()

    def add(record: Any) -> None:
        subject = subject_of(record)
        key = subject.forms
        if subject and key not in seen:
            seen.add(key)
            out.append(subject)

    catalog = docs.get("data/catalog.json")
    for model in catalog.get("models", []) if isinstance(catalog, dict) and isinstance(catalog.get("models"), list) else []:
        add(model)
    lifecycle = docs.get("data/lifecycle.json")
    for event in lifecycle.get("events", []) if isinstance(lifecycle, dict) and isinstance(lifecycle.get("events"), list) else []:
        add(event)
    return tuple(out)
