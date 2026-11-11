"""The `tally` commands: `add`, `list`, `remove` and `rename`."""
import re
import sys

from tally import store

NAME = re.compile(r"[a-z0-9-]+")


def fail(message):
    print(f"tally: {message}", file=sys.stderr)
    return 1


def valid_name(name):
    return NAME.fullmatch(name) is not None


def add(args):
    if not 1 <= len(args) <= 2:
        return fail("usage: tally add <name> [n]")
    name = args[0]
    if not valid_name(name):
        return fail(f"bad name: {name}")
    try:
        n = int(args[1]) if len(args) == 2 else 1
    except ValueError:
        return fail(f"not a number: {args[1]}")
    if n < 0:
        return fail("n must be at least 0")
    counts = store.load()
    counts[name] = counts.get(name, 0) + n
    store.save(counts)
    print(f"{name} {counts[name]}")
    return 0


def list_counts(args):
    if args:
        return fail("usage: tally list")
    for name, n in sorted(store.load().items(), key=lambda item: (-item[1], item[0])):
        print(f"{name} {n}")
    return 0


def remove(args):
    if len(args) != 1:
        return fail("usage: tally remove <name>")
    counts = store.load()
    counts.pop(args[0], None)
    store.save(counts)
    return 0


def rename(args):
    if len(args) != 2:
        return fail("usage: tally rename <old> <new>")
    old, new = args
    counts = store.load()
    if old not in counts:
        print(f"tally: no count: {old}")
        return 1
    counts[new] = counts.pop(old)
    store.save(counts)
    print(f"{new} {counts[new]}")
    return 0


COMMANDS = {"add": add, "list": list_counts, "remove": remove, "rename": rename}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        return fail("usage: tally <add|list|remove|rename> ...")
    return COMMANDS[argv[0]](argv[1:])
