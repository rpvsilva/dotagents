"""Filters and sort shared by `todo list`, `todo search` and `todo export`."""
from datetime import date

from todo.cli.commands.add import tag_list
from todo.storage import PRIORITIES

SORTS = ("priority", "due")
PRIORITY_RANK = {level: rank for rank, level in enumerate(PRIORITIES)}  # unset ranks last


def add_filter_arguments(parser) -> None:
    """Add --tag, --priority, --open/--done, --search and --sort to a command's parser."""
    parser.add_argument("--tag", type=tag_list, action="extend", default=[], dest="tags")
    parser.add_argument("--priority", choices=PRIORITIES)
    state = parser.add_mutually_exclusive_group()
    state.add_argument("--open", action="store_true")
    state.add_argument("--done", action="store_true")
    parser.add_argument("--search")
    parser.add_argument("--sort", choices=SORTS)


def matches(item, args) -> bool:
    """True when the item passes every filter given in args."""
    if not set(args.tags) <= set(item.tags):
        return False
    if args.priority and item.priority != args.priority:
        return False
    if args.open and item.done or args.done and not item.done:
        return False
    if args.search:
        text = args.search.lower()
        if text not in item.title.lower() and not any(text in tag for tag in item.tags):
            return False
    return True


def sort_key(sort: str):
    if sort == "priority":
        return lambda i: (PRIORITY_RANK.get(i.priority, len(PRIORITIES)), i.id)
    return lambda i: (i.due is None, i.due or date.max, i.id)


def select_items(items, args) -> list:
    """The items that match args' filters, in args' sort order (by id by default)."""
    selected = [i for i in items if matches(i, args)]
    return sorted(selected, key=sort_key(args.sort) if args.sort else lambda i: i.id)
