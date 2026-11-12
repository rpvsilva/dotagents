"""The todo Item and the date, tag, priority and recurrence rules around it."""
import calendar
import re
import reprlib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

DATE_FORMAT = "%Y-%m-%d"
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_TAGS = 10
# Largest item id: exact in JSON readers that use doubles, and far below Python's int-to-text limit.
MAX_ID = 2**53
PRIORITIES = ("high", "medium", "low")
PRIORITY_MARKERS = {"high": "!!", "medium": "!"}  # low and unset show nothing
RULE_UNITS = ("day", "week", "month", "year")
RULE_PATTERN = re.compile(r"(?:([0-9]+)\s+)?([a-z]+)")


_SHORT_REPR = reprlib.Repr()
_SHORT_REPR.maxlevel = 2
_SHORT_REPR.maxstring = _SHORT_REPR.maxother = 60
_SHORT_REPR.maxlist = _SHORT_REPR.maxdict = 4


def show(value) -> str:
    """A short repr for error messages: a stored value may be megabytes long or deeply nested."""
    try:
        return _SHORT_REPR.repr(value)
    except (ValueError, RecursionError):  # e.g. an int too long to turn into text
        return f"<{type(value).__name__}>"


class StoreError(ValueError):
    """The todo file holds a value that cannot be loaded."""


class ItemNotFound(KeyError):
    """No item has the given id; args[0] is that id."""


class NotRecurring(ValueError):
    """The item has no recurrence rule; args[0] is its id."""

    def __str__(self) -> str:
        return f"item {self.args[0]} is not recurring"


def parse_date(value: str) -> date:
    """Parse a strict YYYY-MM-DD date; raises ValueError otherwise."""
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise ValueError(f"not a YYYY-MM-DD date: {show(value)}")
    return datetime.strptime(value, DATE_FORMAT).date()


def normalize_title(value: str) -> str:
    """Replace each non-printable character (control codes, newlines) with `?`, so a title cannot drive the terminal or split an export line."""
    if not isinstance(value, str):
        raise ValueError(f"title is not text: {show(value)}")
    return "".join(c if c.isprintable() else "?" for c in value)


def normalize_tag(value: str) -> str:
    """Trim and lowercase a tag; raises ValueError if it is empty or holds whitespace or a non-printable character."""
    if not isinstance(value, str):
        raise ValueError(f"not a tag: {show(value)}")
    tag = value.strip().lower()
    if not tag or any(c.isspace() or not c.isprintable() for c in tag):
        raise ValueError(f"invalid tag {show(value)}, expected a word with no spaces")
    return tag


def normalize_tags(values) -> list[str]:
    """Normalise each tag and drop duplicates, keeping first order; at most MAX_TAGS."""
    tags = list(dict.fromkeys(normalize_tag(v) for v in values))
    if len(tags) > MAX_TAGS:
        raise ValueError(f"too many tags ({len(tags)}), an item holds at most {MAX_TAGS}")
    return tags


def parse_priority(value):
    """Return a priority word from PRIORITIES, or None for None; raises ValueError otherwise."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in PRIORITIES:
        raise ValueError(f"invalid priority {show(value)}, expected one of {', '.join(PRIORITIES)}")
    return value


def parse_rule(text) -> tuple[int, str]:
    """Parse a recurrence rule like `month`, `2 weeks` or `day` into (n, unit).

    The unit is one of RULE_UNITS, singular or plural; N is optional (default 1)
    and must be positive. Raises ValueError otherwise.
    """
    match = RULE_PATTERN.fullmatch(text.strip().lower()) if isinstance(text, str) else None
    unit = match and match.group(2)
    if unit and unit.endswith("s"):
        unit = unit[:-1]
    if not match or unit not in RULE_UNITS:
        raise ValueError(
            f"invalid rule {show(text)}, expected [N] {'|'.join(RULE_UNITS)}, e.g. month or 2 weeks"
        )
    n = int(match.group(1) or 1)
    if n <= 0:
        raise ValueError(f"invalid rule {show(text)}, N must be a positive number")
    return n, unit


def format_rule(n: int, unit: str) -> str:
    """The rule as users write it: `month` for 1, `2 weeks` for more."""
    return unit if n == 1 else f"{n} {unit}s"


def normalize_rule(text):
    """A rule in its canonical form (`month`, `2 weeks`), or None for None; raises ValueError."""
    return None if text is None else format_rule(*parse_rule(text))


def parse_anchor_day(value):
    """Return a day of month 1..31, or None for None; raises ValueError otherwise."""
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 31:
        raise ValueError(f"invalid anchor_day {show(value)}, expected a day of month 1..31")
    return value


def new_series_id() -> str:
    """A fresh series id, unique across machines (unlike item ids)."""
    return uuid.uuid4().hex


def parse_series_id(value):
    """Return a non-empty series id string, or None for None; raises ValueError otherwise."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid series_id {show(value)}, expected a non-empty string")
    return value


def parse_occurrence(value):
    """Return a positive occurrence number, or None for None; raises ValueError otherwise."""
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError(f"invalid occurrence {show(value)}, expected a positive whole number")
    return value


def _add_months(start: date, months: int, anchor_day: int) -> date:
    """`start` moved by `months`, on `anchor_day` clamped to the month's last day."""
    total = start.year * 12 + start.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def next_due(due: date, every: str, today: date, anchor_day: int | None = None) -> date:
    """The next due date of a series: `due` plus the rule's interval, stepped until >= today.

    Missed occurrences are skipped. Month and year steps land on `anchor_day`
    (default: `due`'s day), clamped to the last day of a shorter month.
    Raises ValueError when the next date would pass date.max (9999-12-31).
    """
    n, unit = parse_rule(every)
    anchor_day = anchor_day or due.day
    step = 0
    while True:
        step += 1
        try:
            if unit == "day":
                nxt = due + timedelta(days=n * step)
            elif unit == "week":
                nxt = due + timedelta(weeks=n * step)
            else:
                nxt = _add_months(due, n * step * (12 if unit == "year" else 1), anchor_day)
        except (OverflowError, ValueError):
            raise ValueError(
                f"every {format_rule(n, unit)} from {due.isoformat()} goes past"
                f" {date.max.isoformat()}, the last date todo can store"
            ) from None
        if nxt >= today:
            return nxt


@dataclass
class Item:
    id: int
    title: str
    done: bool = False
    due: date | None = None
    # Last change, in UTC; sync uses it for last-write-wins. None = never stamped.
    updated_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    priority: str | None = None  # one of PRIORITIES, or None
    # Recurrence rule in canonical form (`month`, `2 weeks`), or None for a one-off.
    every: str | None = None
    # Day of month the series started on, so monthly and yearly steps clamp
    # back to it (Jan 31 -> Feb 28 -> Mar 31). None = the due date's own day.
    anchor_day: int | None = None
    # Hidden series identity for sync: the series id is set once at `add --every`
    # and shared by every occurrence; the occurrence number starts at 1 and goes
    # up by one on each `complete` (on the next item) and `skip` (in place).
    # Sync drops duplicates with the same pair. None on one-off items.
    series_id: str | None = None
    occurrence: int | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_overdue(item: Item, today: date) -> bool:
    """Open, has a due date, and that date is before today."""
    return not item.done and item.due is not None and item.due < today


def is_due_soon(item: Item, today: date, days: int = 3) -> bool:
    """Open, has a due date, and that date is within today .. today + days."""
    return (
        not item.done
        and item.due is not None
        and today <= item.due <= today + timedelta(days=days)
    )
