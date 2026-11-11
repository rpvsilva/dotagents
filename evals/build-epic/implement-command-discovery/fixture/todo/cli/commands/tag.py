"""todo tag ID [+TAG[,TAG...] | -TAG[,TAG...] | TAG[,TAG...]]...: add (+ or no sign) and remove (-) tags."""
import argparse

from todo.cli.errors import UsageError
from todo.storage import StoreError, normalize_tag


def register(parser) -> None:
    parser.add_argument("id", type=int)
    # REMAINDER so "-home" (and "-h") reach us as removals, not as options.
    parser.add_argument("changes", nargs=argparse.REMAINDER, metavar="+TAG|-TAG")


def parse_changes(changes: list[str]) -> tuple[list[str], list[str]]:
    """Split changes into (add, remove), each tag normalised; raises UsageError."""
    if not changes:
        raise UsageError("no tag changes given, expected +TAG or -TAG")
    add, remove = [], []
    for change in changes:
        if change.startswith("-"):
            target, value = remove, change[1:]
        else:
            target, value = add, change.removeprefix("+")
        try:
            # "+a,b" and "-a,b" act on each tag, like `add --tag a,b`.
            target.extend(normalize_tag(part) for part in value.split(","))
        except ValueError:
            raise UsageError(
                f"invalid tag {change!r}, expected +TAG or -TAG, words with no spaces"
                " separated by commas"
            ) from None
    return add, remove


def run(args, ctx) -> None:
    add, remove = parse_changes(args.changes)
    try:
        item = ctx.store.update_tags(args.id, add=add, remove=remove)
    except StoreError:
        raise  # a bad todo file: main exits 1
    except ValueError as e:  # a tag both added and removed, or more than MAX_TAGS
        raise UsageError(str(e)) from None
    tags = " ".join(f"#{t}" for t in item.tags)
    ctx.out(f"tagged {item.id}:" + (f" {tags}" if tags else ""))
