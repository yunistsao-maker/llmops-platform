"""Portal facts that are the platform team's decisions and its own measurements, not research findings.

``platformStatus`` says what the team decided about a model on their own gateway; ``litellm.status: tested-*`` says how
the team's own gateway test of the model went; a fact labelled ``verification: internal-test`` and a benchmark
``measuredBy: internal`` are numbers the team measured themselves.  Research can establish none of them, so no proposal
may add, change or remove them - whatever the model was told, whatever a web page said - and none may turn the record
that carries such a measurement into another one (the same quota "of" another cloud's offering is another fact).
The rule is enforced in code in three places that all call this module: the agent's validator (the model gets the
error and can fix its proposal), the replay used by ``scripts/apply_proposal.py`` and the publishing pipeline (which
trusts no proposal).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterator

CATALOG = "data/catalog.json"
#: what a model that research adds to the catalog starts as (the schema: 'Seeded as "evaluating" for new models')
SEED_STATUS = "evaluating"
INTERNAL_LABEL = "internal-test"


def _models(docs: dict[str, Any]) -> dict[str, dict]:
    catalog = docs.get(CATALOG)
    models = catalog.get("models") if isinstance(catalog, dict) else None
    out: dict[str, dict] = {}
    for m in models if isinstance(models, list) else []:
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            out[m["id"]] = m
    return out


#: a benchmark the team ran itself
INTERNAL_MEASURER = "internal"
#: what makes a record the record it is: a measurement belongs to the model / offering / event that carries these
_IDENTITY = ("id", "name", "modelName", "modelId", "vendor", "vendorModelId", "providerModelId", "cloud", "service")
#: how the team's own gateway test of a model went (models[].litellm.status); research writes "researched" or "untested"
_TESTED = "tested"


def _is_internal(node: dict) -> bool:
    measured_by = node.get("measuredBy")
    return node.get("verification") == INTERNAL_LABEL or (isinstance(measured_by, str) and measured_by.strip().lower() == INTERNAL_MEASURER)


def _internal_facts(node: Any, path: str = "", carrier: tuple = ()) -> Iterator[tuple[str, str]]:
    """(where, content) of every fact that is the team's own measurement.

    ``where`` names records by their id and ignores positions in lists, so re-ordering a list is not a change while
    moving the label to another record, editing anything inside the fact, adding one and removing one all are.
    ``content`` includes what identifies the records the fact sits in (``carrier``): the fact is about them.
    """
    if isinstance(node, dict):
        if _is_internal(node):
            yield path or "/", json.dumps({"fact": node, "of": carrier}, sort_keys=True, ensure_ascii=False)
            return  # everything below belongs to this fact
        identity = tuple((k, node[k]) for k in _IDENTITY if isinstance(node.get(k), (str, int)))
        inside = carrier + (identity,) if identity else carrier
        for k, v in node.items():
            yield from _internal_facts(v, f"{path}/{k}", inside)
    elif isinstance(node, list):
        for v in node:
            ident = v["id"] if isinstance(v, dict) and isinstance(v.get("id"), str) else "*"
            yield from _internal_facts(v, f"{path}/{ident}", carrier)


def _test_status(model: dict) -> Any:
    gateway = model.get("litellm")
    return gateway.get("status") if isinstance(gateway, dict) else None


def _is_tested(status: Any) -> bool:
    return isinstance(status, str) and status.strip().lower().startswith(_TESTED)


def protected_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Human-readable violations in going from ``before`` to ``after`` (both: relative path -> parsed JSON)."""
    out: list[str] = []
    old, new = _models(before), _models(after)
    for mid, model in new.items():
        status = model.get("platformStatus")
        if mid in old:
            was = old[mid].get("platformStatus")
            if status != was:
                out.append(f"{CATALOG}: platformStatus of {mid} would change from {was!r} to {status!r}; platformStatus is the platform "
                           "team's decision and cannot be changed by research - leave it as it is")
        elif status != SEED_STATUS:
            out.append(f"{CATALOG}: new model {mid} has platformStatus {status!r}; a model added by research starts as {SEED_STATUS!r} "
                       "(the platform team decides everything after that)")
        tested, was_tested = _test_status(model), _test_status(old[mid]) if mid in old else None
        if tested != was_tested and (_is_tested(tested) or _is_tested(was_tested)):
            out.append(f"{CATALOG}: litellm.status of {mid} would {'change from ' + repr(was_tested) + ' to' if mid in old else 'be'} {tested!r}; "
                       "'tested-...' records the platform team's own gateway test and cannot be set or changed by research "
                       "(what was read from pages is 'researched')")

    why = (f"facts labelled verification {INTERNAL_LABEL!r} and benchmarks measuredBy {INTERNAL_MEASURER!r} are the platform team's own "
           "measurements: research cannot add, change, move or remove them, nor change which model or offering they belong to")
    for rel in sorted(set(before) | set(after)):
        was_facts, now_facts = Counter(_internal_facts(before.get(rel))), Counter(_internal_facts(after.get(rel)))
        was_at = {w for w, _ in was_facts}
        appeared = {w for w, _ in (now_facts - was_facts)}  # a fact that was not there in this form (and number)
        vanished = {w for w, _ in (was_facts - now_facts)}
        for where in sorted(appeared):
            what = "would change" if where in was_at else "would be new"
            out.append(f"{rel}: the fact at {where} {what}; {why}")
        for where in sorted(vanished - appeared):
            out.append(f"{rel}: the fact at {where} would be removed or lose its label; {why}")
    return out
