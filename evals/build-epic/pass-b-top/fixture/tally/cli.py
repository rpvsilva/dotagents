"""The `tally` commands: `add` and `list`."""
import sys

from tally import store


def fail(message):
    print(f"tally: {message}", file=sys.stderr)
    return 1


def add(args):
    if not 1 <= len(args) <= 2:
        return fail("usage: tally add <name> [n]")
    name = args[0]
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


COMMANDS = {"add": add, "list": list_counts}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        return fail("usage: tally <add|list> ...")
    return COMMANDS[argv[0]](argv[1:])
