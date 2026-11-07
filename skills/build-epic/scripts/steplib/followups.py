"""The run's follow-ups in `<run>/followups.jsonl`: one JSON object per line, read and changed only through here.

A report file (an implementer's or a reviewer's) may hold a `## Follow-ups` section, one row per line:

    - <severity> <kind> <path[:line]>: <claim>

`severity` is `blocker`, `should-fix` or `nit`; `kind` is `follow-up` or `interaction`. A row of kind `owner-doc`
proposes a change to a file only the human edits: `- <severity> owner-doc <path>#<item>: <claim>`, followed by a fenced
`diff` block, the ready-to-apply patch for that item (`<item>` names the part of the file it changes, e.g. a heading).
Blank lines in the section are skipped. The section ends at the next `## ` heading or at the first other line that is
not a row, so the report's block or a reviewer's `process:` lines after it are not read; a bullet (`- `, `* `, `+ `)
that is not a row, or a first line that is not a row, makes the section malformed, as does an `owner-doc` row with no
`#<item>` or no non-empty `diff` block closed by its fence. A recorded row holds `id` (1, 2, ... in file order),
`ticket`, `pr` (a number or null), `severity`, `kind`, `file`, `line` (a number or null), `claim` and `source` (the
report's path), plus any key `mark` set (`carried`, `issue`); an `owner-doc` row also holds `item` and `patch` (the
block's lines, each ending in a newline). Rows are unique on (ticket, kind, file, claim). An `owner-doc` row is unique
on its `<path>#<item>` whatever its ticket: a later one with another claim, severity or patch replaces the earlier row,
which is dropped, and takes the next `id`. The carry file is the `interaction` rows not yet `carried`.

`report` groups the rows for the run's final report: counts by area (a row's first path segment) and severity, the
rows grouped the same way, then one section per owner-only file with its rows and one combined patch.

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
KINDS = ("follow-up", "interaction", "owner-doc")
OWNER_DOC = "owner-doc"
_SEVERITY = "|".join(map(re.escape, SEVERITIES))
ROW = re.compile(rf"^- ({_SEVERITY}) ({'|'.join(re.escape(k) for k in KINDS if k != OWNER_DOC)}) "
                 r"(\S+?)(?::(\d+))?: (\S.*?)\s*$")
DOC_ROW = re.compile(rf"^- ({_SEVERITY}) ({re.escape(OWNER_DOC)}) ([^\s#]+)#(\S+?): (\S.*?)\s*$")
FENCE, END = "```diff", "```"
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
    lines, rows, i = m.group(1).splitlines() if m else [], [], 0
    while i < len(lines):
        raw, i = lines[i], i + 1
        if not raw.strip():
            continue
        doc = DOC_ROW.match(raw)
        if doc:
            patch, i = _patch(lines, i, raw)
            severity, kind, path, item, claim = doc.groups()
            rows.append({"severity": severity, "kind": kind, "file": path, "item": item, "line": None,
                         "claim": claim, "patch": patch})
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


def _patch(lines, i, row):
    """The non-empty `diff` block starting at `lines[i]` (blank lines before it skipped) and the index after its
    closing fence; raises `BadRow(row)` when there is none."""
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != FENCE:
        raise BadRow(row.strip())
    body = []
    for j in range(i + 1, len(lines)):
        if lines[j].strip() == END:
            if not body:
                break
            return "".join(line + "\n" for line in body), j + 1
        body.append(lines[j])
    raise BadRow(row.strip())


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


def _same_doc(a, b):
    return all(a.get(k) == b.get(k) for k in ("severity", "claim", "patch"))


def add(run, rows):
    """Appends the rows not recorded yet, each with the next `id`; returns the rows added. Each row holds `ticket`,
    `pr`, `severity`, `kind`, `file`, `line`, `claim` and `source` (an `owner-doc` row also `item` and `patch`). An
    `owner-doc` row replaces the recorded row of its `<path>#<item>` unless it says the same."""
    with lock(run):
        have = load(run)
        seen = {_key(r) for r in have if r["kind"] != OWNER_DOC}
        next_id = max((r["id"] for r in have), default=0) + 1
        added = []
        for row in rows:
            if row["kind"] == OWNER_DOC:
                item = (row["file"], row["item"])
                old = [r for r in have + added if r["kind"] == OWNER_DOC and (r["file"], r["item"]) == item]
                if old and _same_doc(old[-1], row):
                    continue
                have = [r for r in have if r not in old]
                added = [r for r in added if r not in old]
            elif _key(row) in seen:
                continue
            else:
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


def _where(row):
    if row["kind"] == OWNER_DOC:
        return f"{row['file']}#{row['item']}"
    return row["file"] + (f":{row['line']}" if row.get("line") else "")


def _origin(row):
    return f"(ticket {row['ticket']}" + (f", PR #{row['pr']}" if row.get("pr") else "") + ")"


def report(rows, owner_only):
    """The lines of the follow-up report on `rows`. First `follow-ups: <n>`, then one line per area (sorted),
    `<area>: <n> <severity>, ...` in severity order, leaving out a severity with none; a blank line, then per area
    `## <area>`, per severity `### <severity>` and its rows, `- <kind> <path...>: <claim> (ticket TT[, PR #N])`, in
    `id` order. Then, when any row is on an owner-only file (an `owner-doc` row, or a file `owner_only(path)` is true
    for), `## Owner-only files` and per such file (sorted) `### <path>`, its rows with their severity, and its
    `owner-doc` patches joined in `id` order in one fenced `diff` block."""
    out = [f"follow-ups: {len(rows)}"]
    if not rows:
        return out
    rows = sorted(rows, key=lambda r: r["id"])
    areas = {}
    for row in rows:
        areas.setdefault(row["file"].split("/", 1)[0], {}).setdefault(row["severity"], []).append(row)
    for area in sorted(areas):
        out.append(f"{area}: " + ", ".join(f"{len(areas[area][s])} {s}" for s in SEVERITIES if s in areas[area]))
    out.append("")
    for area in sorted(areas):
        out.append(f"## {area}")
        for severity in (s for s in SEVERITIES if s in areas[area]):
            out.append(f"### {severity}")
            out += [f"- {r['kind']} {_where(r)}: {r['claim']} {_origin(r)}" for r in areas[area][severity]]
    owned = {}
    for row in rows:
        if row["kind"] == OWNER_DOC or owner_only(row["file"]):
            owned.setdefault(row["file"], []).append(row)
    if owned:
        out.append("## Owner-only files")
    for path in sorted(owned):
        out.append(f"### {path}")
        out += [f"- {r['severity']} {r['kind']} {_where(r)}: {r['claim']} {_origin(r)}" for r in owned[path]]
        patch = "".join(r["patch"] for r in owned[path] if r["kind"] == OWNER_DOC)
        if patch:
            out += ["```diff", *patch.splitlines(), "```"]
    return out
