"""The store: a JSON object mapping each name to its count, at `TALLY_FILE` or `~/.tally.json`."""
import json
import os
from pathlib import Path


def path():
    return Path(os.environ.get("TALLY_FILE") or Path.home() / ".tally.json")


def load():
    try:
        return json.loads(path().read_text())
    except FileNotFoundError:
        return {}


def save(counts):
    p = path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")
    tmp.replace(p)
