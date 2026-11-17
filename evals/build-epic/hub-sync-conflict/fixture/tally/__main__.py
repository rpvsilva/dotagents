import importlib
import json
import os
import sys

from tally.commands import COMMANDS


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: tally {" + ",".join(COMMANDS) + "}", file=sys.stderr)
        return 2
    path = os.environ.get("TALLY_FILE", "tally.json")
    items = json.load(open(path)) if os.path.exists(path) else []
    for line in importlib.import_module(f"tally.{argv[0]}").run(items):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
