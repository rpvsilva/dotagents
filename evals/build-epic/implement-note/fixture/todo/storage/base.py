"""The Store interface: backends implement load and save, the rest is shared."""
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Callable, Iterable

from todo.storage.model import (
    MAX_ID,
    Item,
    ItemNotFound,
    NotRecurring,
    new_series_id,
    next_due,
    normalize_rule,
    normalize_tag,
    normalize_tags,
    normalize_title,
    parse_priority,
    utc_now,
)


def _next_id(items: list[Item]) -> int:
    """One more than the largest id; raises ValueError past MAX_ID, so a saved file always loads again."""
    nxt = max((i.id for i in items), default=0) + 1
    if nxt > MAX_ID:
        raise ValueError(f"no free id: ids go up to {MAX_ID}")
    return nxt


def _next_occurrence(item: Item) -> int | None:
    """The occurrence number after `item`'s; None when it has none (no series id)."""
    return None if item.occurrence is None else item.occurrence + 1


class Store(ABC):
    # Clock for updated_at stamps; tests replace it.
    clock: Callable[[], datetime] = staticmethod(utc_now)

    @abstractmethod
    def load(self) -> list[Item]:
        """Return every stored item; raises StoreError on bad stored data."""

    @abstractmethod
    def save(self, items: list[Item]) -> None:
        """Replace the stored items with `items`."""

    def add(
        self,
        title: str,
        due: date | None = None,
        tags: Iterable[str] = (),
        priority: str | None = None,
        every: str | None = None,
        today: date | None = None,
        parent: int | None = None,
    ) -> Item:
        """Append a new item; raises ValueError on a bad tag, more than MAX_TAGS, a bad priority or a bad rule.

        Raises ItemNotFound when `parent` is set and no item has that id; nothing is saved then.
        A done item may be a parent.

        A bad rule includes one whose next occurrence would fall after 9999-12-31.

        A recurring item (`every` set) without `due` is due `today`, the caller's
        local date; it is required then.
        """
        title = normalize_title(title)
        tags = normalize_tags(tags)
        priority = parse_priority(priority)
        every = normalize_rule(every)
        if every is not None and due is None:
            if today is None:
                raise ValueError("a recurring item without a due date needs today")
            due = today
        if every is not None:
            next_due(due, every, due)  # reject a rule whose next date is out of range
        items = self.load()
        if parent is not None and not any(i.id == parent for i in items):
            raise ItemNotFound(parent)
        item = Item(
            id=_next_id(items),
            title=title,
            due=due,
            updated_at=self.clock(),
            tags=tags,
            priority=priority,
            every=every,
            series_id=None if every is None else new_series_id(),
            occurrence=None if every is None else 1,
            parent=parent,
        )
        self.save(items + [item])
        return item

    def complete(self, item_id: int, today: date | None = None) -> Item | None:
        """Mark an item done; for a recurring item, add and return its next occurrence.

        `today` is the caller's local date, required for a recurring item.
        Completing an item that is already done changes nothing. Raises
        ItemNotFound on a missing id.
        """
        items = self.load()
        for i in items:
            if i.id == item_id:
                break
        else:
            raise ItemNotFound(item_id)
        if i.done:
            return None
        if i.every is not None and today is None:
            raise ValueError("completing a recurring item needs today")
        stamp = self.clock()
        i.done = True
        i.snoozed_until = None
        i.updated_at = stamp
        nxt = None
        if i.every is not None:
            anchor_day = i.anchor_day or (i.due or today).day
            nxt = Item(
                id=_next_id(items),
                title=i.title,
                due=next_due(i.due or today, i.every, today, anchor_day),
                updated_at=stamp,
                tags=list(i.tags),
                priority=i.priority,
                every=i.every,
                anchor_day=anchor_day,
                series_id=i.series_id,
                occurrence=_next_occurrence(i),
                parent=i.parent,
                blocked_on=list(i.blocked_on),
            )
            items.append(nxt)
        self.save(items)
        return nxt

    def skip(self, item_id: int, today: date) -> Item:
        """Move a recurring item to its next occurrence without marking it done.

        Same id; `due` moves by the rules `complete` uses (stepped to `today`,
        the caller's local date, or later; clamped on the series' anchor day).
        Raises ItemNotFound on a missing id, NotRecurring on a one-off item,
        ValueError on a done item; nothing is saved then.
        """
        items = self.load()
        for i in items:
            if i.id == item_id:
                break
        else:
            raise ItemNotFound(item_id)
        if i.every is None:
            raise NotRecurring(item_id)
        if i.done:
            raise ValueError(f"item {item_id} is already done, nothing to skip")
        anchor_day = i.anchor_day or (i.due or today).day
        i.due = next_due(i.due or today, i.every, today, anchor_day)
        i.anchor_day = anchor_day
        i.occurrence = _next_occurrence(i)
        i.snoozed_until = None
        i.updated_at = self.clock()
        self.save(items)
        return i

    def update(self, item_id: int, change: Callable[[Item, list[Item]], bool]) -> Item:
        """Load, find the item, call `change(item, items)`; when it returns true, stamp and save.

        `change` edits the item in place and returns whether it changed anything: false saves
        nothing and stamps nothing. An exception from `change` saves nothing. Raises
        ItemNotFound on a missing id. Returns the item.
        """
        items = self.load()
        for i in items:
            if i.id == item_id:
                break
        else:
            raise ItemNotFound(item_id)
        if change(i, items):
            i.updated_at = self.clock()
            self.save(items)
        return i

    def update_tags(self, item_id: int, add: Iterable[str], remove: Iterable[str]) -> Item:
        """Add and remove tags on an item; removing an absent tag is a no-op.

        Raises ItemNotFound on a missing id, ValueError on a bad tag, a tag
        both added and removed, or more than MAX_TAGS; nothing is saved then.
        """
        add = [normalize_tag(t) for t in add]  # MAX_TAGS applies to the result only
        remove = {normalize_tag(t) for t in remove}
        both = [t for t in dict.fromkeys(add) if t in remove]
        if both:
            raise ValueError(
                "cannot both add and remove tag " + ", ".join(repr(t) for t in both)
            )
        items = self.load()
        for i in items:
            if i.id == item_id:
                i.tags = normalize_tags([t for t in i.tags if t not in remove] + add)
                i.updated_at = self.clock()
                self.save(items)
                return i
        raise ItemNotFound(item_id)

    def set_priority(self, item_id: int, priority: str | None) -> None:
        """Set or clear (None) an item's priority; raises ValueError on a bad value, ItemNotFound on a missing id."""
        priority = parse_priority(priority)
        items = self.load()
        for i in items:
            if i.id == item_id:
                i.priority = priority
                i.updated_at = self.clock()
                self.save(items)
                return
        raise ItemNotFound(item_id)

    def stop(self, item_id: int) -> Item:
        """End an item's series: clear its rule, keep `done` and `due` as they are.

        The item becomes a one-off, so completing it later creates nothing.
        An open item keeps its hidden `series_id` and `occurrence`: a series id
        with no rule tells sync the series stopped at that occurrence, so a next
        occurrence another machine created offline loses its rule too. A done
        item drops them, since its series goes on in the next occurrence.
        Raises ItemNotFound on a missing id, NotRecurring on a one-off item;
        nothing is saved then.
        """
        items = self.load()
        for i in items:
            if i.id == item_id:
                if i.every is None:
                    raise NotRecurring(item_id)
                i.every = None
                i.anchor_day = None
                if i.done:
                    i.series_id = None
                    i.occurrence = None
                i.updated_at = self.clock()
                self.save(items)
                return i
        raise ItemNotFound(item_id)
