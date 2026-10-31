"""contracts/events.md and SUBSCRIPTIONS in llmops_common.queue must stay identical, and the notifier must render
exactly the events it is subscribed to."""
import re

from conftest import ROOT

from llmops_common.queue import SUBSCRIPTIONS
from notifier.messages import RENDERERS


def _events_table() -> dict[str, tuple[str, ...]]:
    table = {}
    for line in (ROOT / "contracts/events.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([a-z0-9_.]+)` \| [^|]+ \| ([^|]+) \|", line)
        if m:
            table[m.group(1)] = tuple(s.strip() for s in m.group(2).split(","))
    return table


def test_events_md_matches_subscriptions():
    assert _events_table() == SUBSCRIPTIONS


def test_notifier_renders_exactly_its_subscriptions():
    assert set(RENDERERS) == {event for event, queues in SUBSCRIPTIONS.items() if "notifier" in queues}
