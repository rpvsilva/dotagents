"""The run's rules in `<run>/decisions.md`: the reader every step and prompt uses. `decide.py` is its only writer.

The file is header lines, an Authorizations table and dated verbatim Decisions:

    # Decisions
    base: main
    ci: on

    ## Authorizations

    | clause | waived | quote |
    | --- | --- | --- |
    | Leave merges to the human | yes | Rui: "merge them as the App" |

    ## Decisions

    - 2026-11-06: Merge each PR, use the cli like it was me

A table cell escapes `\\` and `|` with a backslash, so a quote reads back byte for byte. A decision spanning lines
continues on lines indented by two spaces. Reading a file and rendering it again gives the same file.
"""
import datetime
import os
import tempfile
from collections import namedtuple

from . import core

NAME = "decisions.md"
TITLE = "# Decisions"
KEYS = ("base", "ci", "merge_identity", "human", "review_before_agent_merge", "test_command")
CHOICES = {"ci": ("on", "off"), "merge_identity": ("app", "human"), "review_before_agent_merge": ("yes", "no")}
WAIVED = ("yes", "no", "ask")
AUTHORIZATIONS = "## Authorizations"
DECISIONS = "## Decisions"
TABLE_HEAD = "| clause | waived | quote |\n| --- | --- | --- |\n"

Authorization = namedtuple("Authorization", "clause waived quote")
Decision = namedtuple("Decision", "date text")


class Decisions:
    """One `decisions.md`: `headers` (key to value, set keys only), `authorizations` and `decisions`, in file order."""

    def __init__(self, headers=None, authorizations=None, decisions=None):
        self.headers = dict(headers or {})
        self.authorizations = list(authorizations or [])
        self.decisions = list(decisions or [])

    def get(self, key):
        """Header `key`'s value, or None when unset. An unknown key raises KeyError."""
        if key not in KEYS:
            raise KeyError(key)
        return self.headers.get(key) or None

    def waived(self, clause):
        """`yes`, `no` or `ask` for `clause`, or None when the table has no row for it."""
        for row in self.authorizations:
            if row.clause == clause:
                return row.waived
        return None

    def __eq__(self, other):
        return isinstance(other, Decisions) and (self.headers, self.authorizations, self.decisions) == (
            other.headers, other.authorizations, other.decisions)


def path(run):
    return core.resolve(run) / NAME


def load(run):
    """The run's decisions, or None when `<run>/decisions.md` does not exist."""
    p = path(run)
    if not p.exists():
        return None
    with open(p, newline="") as f:
        return parse(f.read())


def get(run, key):
    """Header `key` of the run's decisions, or None when it is unset or the file is missing."""
    d = load(run)
    if d is None:
        if key not in KEYS:
            raise KeyError(key)
        return None
    return d.get(key)


def _escape(cell):
    return cell.replace("\\", "\\\\").replace("|", "\\|")


def _cells(line):
    """The cells of a table row, unescaped; each keeps all but the one space of padding on each side."""
    cells, cur, i = [], [], line.index("|") + 1
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    return [c[1:-1] if len(c) >= 2 and c[0] == " " and c[-1] == " " else c.strip() for c in cells]


def parse(text):
    """A `Decisions` from the text of a `decisions.md`. Lines it does not know are ignored."""
    d, section = Decisions(), None
    for line in text.split("\n"):
        if line == AUTHORIZATIONS:
            section = "auth"
        elif line == DECISIONS:
            section = "dec"
        elif line.startswith("#"):
            section = None if line != TITLE else section
        elif section is None:
            key, sep, value = line.partition(": ")
            if sep and key in KEYS:
                d.headers[key] = value
            elif line.endswith(":") and line[:-1] in KEYS:
                d.headers[line[:-1]] = ""
        elif section == "auth" and line.startswith("|"):
            cells = _cells(line)
            if len(cells) == 3 and cells[1] in WAIVED:
                d.authorizations.append(Authorization(*cells))
        elif section == "dec":
            if line.startswith("- ") and line[12:14] == ": " and _is_date(line[2:12]):
                d.decisions.append(Decision(line[2:12], line[14:]))
            elif line.startswith("  ") and d.decisions:
                last = d.decisions.pop()
                d.decisions.append(Decision(last.date, last.text + "\n" + line[2:]))
    return d


def _is_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def render(d):
    """The text of `decisions.md` for `d`: headers in `KEYS` order, then the two sections."""
    out = [TITLE + "\n"]
    out += [f"{k}: {d.headers[k]}\n" for k in KEYS if k in d.headers]
    out.append(f"\n{AUTHORIZATIONS}\n\n{TABLE_HEAD}")
    out += [f"| {_escape(a.clause)} | {a.waived} | {_escape(a.quote)} |\n" for a in d.authorizations]
    out.append(f"\n{DECISIONS}\n\n")
    out += [f"- {x.date}: {x.text.replace(chr(10), chr(10) + '  ')}\n" for x in d.decisions]
    return "".join(out)


def check_value(key, value):
    """Raises `core.Fail` unless `value` may be header `key`'s value."""
    if key not in KEYS:
        raise core.Fail(f"unknown-key: {key} (one of {', '.join(KEYS)})")
    if "\n" in value or "\r" in value:
        raise core.Fail(f"newline: {key}")
    if key in CHOICES and value not in CHOICES[key]:
        raise core.Fail(f"bad-value: {key} is one of {'|'.join(CHOICES[key])}")


def save(run, d):
    """Writes `d` to `<run>/decisions.md` atomically (temp file in the same folder, then rename)."""
    p = path(run)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{NAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            f.write(render(d))
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def lock(run):
    """The lock every change to `<run>/decisions.md` holds from read to write."""
    return core.file_lock(core.resolve(run) / ".decisions.lock")
