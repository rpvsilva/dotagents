"""Last-write-wins merge of two item lists.

Items are matched by `id`. When both sides hold an item, the one with the
later `updated_at` wins as a whole: tags and priority travel with the item,
never merged field by field. An unstamped item (None) loses to any stamp,
and a tie keeps the local copy. Known limit: two sides that each add a new
item offline both get the next free id, so one overwrites the other.

Recurring items: every occurrence of a series carries the series' hidden
`series_id` (set once at `todo add --every`) and an `occurrence` number;
`complete` gives the next item the number plus one, and `skip` raises it by
one on the same item. After the id merge, items with the same
(series_id, occurrence) are duplicates of one occurrence: only the one with
the latest `updated_at` survives (a tie keeps the lower id). So two sides
that complete the same occurrence offline keep exactly one next occurrence,
and between a skip on one side and a completion on the other, the later
change wins. A removed duplicate counts as a change on each side that held
it, so both sides are saved.

Stop wins: `todo stop` on an open occurrence keeps its `series_id` and
`occurrence` but clears its rule. An item like that, on either side, means
the series stopped there, so every item of that series at that occurrence or
later loses its rule (it stays as a one-off, nothing is deleted), whichever
change is more recent. So an occurrence the other side created offline by
completing the stopped one does not restart the series. Limit: a side that skipped and then completed
counts one ahead of a side that only completed, so both next occurrences
survive.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from todo.storage import Item

NEVER = datetime.min.replace(tzinfo=timezone.utc)


def stamp(item: Item) -> datetime:
    return item.updated_at or NEVER


@dataclass
class SyncResult:
    items: list[Item]
    pulled: list[int] = field(default_factory=list)  # ids changed locally
    pushed: list[int] = field(default_factory=list)  # ids changed on the remote


def drop_duplicate_occurrences(items: list[Item]) -> list[Item]:
    """Keep one item per (series_id, occurrence): the latest `updated_at`, a tie keeps the first."""
    latest: dict[tuple[str, int], Item] = {}
    for item in items:
        if item.series_id is None or item.occurrence is None:
            continue
        key = (item.series_id, item.occurrence)
        if key not in latest or stamp(item) > stamp(latest[key]):
            latest[key] = item
    return [
        i for i in items
        if i.series_id is None or i.occurrence is None
        or latest[(i.series_id, i.occurrence)] is i
    ]


def stopped_series(items: list[Item]) -> dict[str, int]:
    """Series stopped by `todo stop`, each with the first occurrence that has no rule."""
    stopped: dict[str, int] = {}
    for i in items:
        if i.series_id is not None and i.occurrence is not None and i.every is None:
            stopped[i.series_id] = min(i.occurrence, stopped.get(i.series_id, i.occurrence))
    return stopped


def end_stopped_series(items: list[Item], stopped: dict[str, int]) -> list[Item]:
    """Clear the rule on every item at or after the occurrence its series stopped at."""
    return [
        replace(i, every=None, anchor_day=None)
        if i.every is not None
        and i.series_id in stopped
        and (i.occurrence or 0) >= stopped[i.series_id]
        else i
        for i in items
    ]


def merge(local: list[Item], remote: list[Item]) -> SyncResult:
    local_by_id = {i.id: i for i in local}
    remote_by_id = {i.id: i for i in remote}
    winners = []
    for item_id in sorted(local_by_id.keys() | remote_by_id.keys()):
        mine, theirs = local_by_id.get(item_id), remote_by_id.get(item_id)
        if theirs is None:
            winners.append(mine)
        elif mine is None:
            winners.append(theirs)
        else:
            winners.append(theirs if stamp(theirs) > stamp(mine) else mine)
    stopped = stopped_series(local + remote)
    result = SyncResult(items=end_stopped_series(drop_duplicate_occurrences(winners), stopped))
    merged_by_id = {i.id: i for i in result.items}
    # A side changes where its copy differs from the result, removals included.
    for item_id in sorted(local_by_id.keys() | remote_by_id.keys()):
        merged = merged_by_id.get(item_id)
        if merged != local_by_id.get(item_id):
            result.pulled.append(item_id)
        if merged != remote_by_id.get(item_id):
            result.pushed.append(item_id)
    return result
