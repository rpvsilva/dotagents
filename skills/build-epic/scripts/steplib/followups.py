"""The run's follow-ups in `<run>/followups.jsonl`: one JSON object per line, read and changed only through here.

A report file (an implementer's or a reviewer's) may hold a `## Follow-ups` section, one row per line:

    - <severity> <kind> <path[:line]>: <claim>

`severity` is `blocker`, `should-fix` or `nit`; `kind` is `follow-up` or `interaction`. Blank lines in the section are
skipped. The section ends at the next `## ` heading or at the first other line that is not a row, so the report's
block or a reviewer's `process:` lines after it are not read; a bullet (`- `, `* `, `+ `) that is not a row, or a
first line that is not a row, makes the section malformed. A recorded row holds `id` (1, 2, ... in file
order), `ticket`, `pr` (a number or null), `severity`, `kind`, `file`, `line` (a number or null), `claim` and `source`
(the report's path), plus any key `mark` set (`carried`, `issue`). Rows are unique on (ticket, kind, file, claim).
The carry file is the `interaction` rows not yet `carried`.

Every change rewrites the file atomically (temp file in the run folder, then rename) under `<run>/.followups.lock`,
so a reader never sees half a file and two writers never lose each other's rows.
"""
import json
import os
import re
import tempfile

from . import core
from .runfolder import norm

NAME = "followups.jsonl"
SECTION = "Follow-ups"
SEVERITIES = ("blocker", "should-fix", "nit")
KINDS = ("follow-up", "interaction")
ROW = re.compile(rf"^- ({'|'.join(map(re.escape, SEVERITIES))}) ({'|'.join(map(re.escape, KINDS))}) "
                 r"(\S+?)(?::(\d+))?: (\S.*?)\s*$")
BULLET = re.compile(r"^\s*[-*+]\s")


class BadRow(ValueError):
    """A line of a `## Follow-ups` section that is not a row; `line` is the line as written."""

    def __init__(self, line):
        super().__init__(line)
        self.line = line


def parse(text):
    """The rows of `text`'s `## Follow-ups` section as dicts (`severity`, `kind`, `file`, `line`, `claim`); `[]` when
    there is no section. The section ends at the first line that is neither blank nor a row; raises `BadRow` when that
    line is a bullet or the section's first line."""
    m = re.search(rf"^## {re.escape(SECTION)}[ \t]*\n(.*?)(?=^## |\Z)", text + "\n", re.MULTILINE | re.DOTALL)
    rows = []
    for raw in (m.group(1).splitlines() if m else []):
        if not raw.strip():
            continue
        row = ROW.match(raw)
        if not row:
            if rows and not BULLET.match(raw):
                break
            raise BadRow(raw.strip())
        severity, kind, path, line, claim = row.groups()
        rows.append({"severity": severity, "kind": kind, "file": path, "line": int(line) if line else None,
                     "claim": claim})
    return rows


def _path(run):
    return core.resolve(run) / NAME


def lock(run):
    """The lock every change to `<run>/followups.jsonl` holds from read to write."""
    return core.file_lock(core.resolve(run) / ".followups.lock")


def _key(row):
    return norm(str(row["ticket"])), row["kind"], row["file"], row["claim"]


def load(run):
    """Every recorded row, in file order; `[]` when the file is missing."""
    path = _path(run)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _save(run, rows):
    path = _path(run)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{NAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def add(run, rows):
    """Appends the rows not recorded yet, each with the next `id`; returns the rows added. Each row holds `ticket`,
    `pr`, `severity`, `kind`, `file`, `line`, `claim` and `source`."""
    with lock(run):
        have = load(run)
        seen = {_key(r) for r in have}
        next_id = max((r["id"] for r in have), default=0) + 1
        added = []
        for row in rows:
            if _key(row) in seen:
                continue
            seen.add(_key(row))
            added.append({"id": next_id, **{k: v for k, v in row.items() if k != "id"}})
            next_id += 1
        if added:
            _save(run, have + added)
        return added


def uncarried(run, ticket, kind):
    """The rows of ticket `ticket` (`3`, `03`) and kind `kind` with no `carried` key set."""
    return [r for r in load(run)
            if norm(str(r["ticket"])) == norm(str(ticket)) and r["kind"] == kind and not r.get("carried")]


def mark(run, ids, key, value):
    """Sets `key` to `value` on the rows whose `id` is in `ids`; returns how many it set. `id` itself never changes."""
    if key == "id":
        raise ValueError("a row's id never changes")
    ids = set(ids)
    with lock(run):
        rows = load(run)
        hit = [r for r in rows if r["id"] in ids]
        for r in hit:
            r[key] = value
        if hit:
            _save(run, rows)
        return len(hit)
