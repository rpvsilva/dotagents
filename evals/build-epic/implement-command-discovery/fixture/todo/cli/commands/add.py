"""todo add TITLE [--due YYYY-MM-DD] [--tag TAG[,TAG...]]... [--priority high|medium|low] [--every RULE]"""
import argparse

from todo.cli.errors import UsageError
from todo.storage import MAX_TAGS, PRIORITIES, StoreError, normalize_rule, normalize_tag, parse_date


def due_date(value: str):
    """argparse type for --due: a strict YYYY-MM-DD date."""
    try:
        return parse_date(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, expected YYYY-MM-DD"
        ) from None


def tag_list(value: str) -> list[str]:
    """argparse type for --tag: comma-separated tags, each trimmed and lowercased."""
    try:
        return [normalize_tag(part) for part in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid tag {value!r}, expected words with no spaces, separated by commas"
        ) from None


def register(parser) -> None:
    parser.add_argument("title")
    parser.add_argument("--due", type=due_date)
    parser.add_argument("--tag", type=tag_list, action="extend", default=[], dest="tags")
    parser.add_argument("--priority", choices=PRIORITIES)
    # nargs="+" takes every following word up to the next option, as a list; run joins
    # them with spaces, so both `--every 2 weeks` and `--every "2 weeks"` work, and
    # parse_rule rejects anything that is not `[N] unit` (e.g. three words).
    parser.add_argument("--every", nargs="+", metavar="RULE")


def run(args, ctx) -> None:
    tags = list(dict.fromkeys(args.tags))  # each one already normalised by tag_list
    if len(tags) > MAX_TAGS:
        raise UsageError(f"too many tags ({len(tags)}), an item holds at most {MAX_TAGS}")
    try:
        every = normalize_rule(" ".join(args.every)) if args.every else None
    except ValueError as e:
        raise UsageError(str(e)) from None
    try:
        item = ctx.store.add(
            args.title, due=args.due, tags=tags, priority=args.priority, every=every, today=ctx.today
        )
    except StoreError:
        raise  # a bad todo file: main exits 1
    except ValueError as e:  # a rule whose next date is out of range
        raise UsageError(str(e)) from None
    ctx.out(f"added {item.id}: {item.title}")
