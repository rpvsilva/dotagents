#!/usr/bin/env python3
"""Records the follow-ups a report file holds in `<run>/followups.jsonl`, through `steplib/followups.py`.

Usage:
  python3 -I followups.py <run> record <report> --ticket TT [--pr N]
  python3 -I followups.py <run> fixed <id>... [--task <task>]
  python3 -I followups.py <run> report

`record` reads the `## Follow-ups` section of `<report>` (an implementer's or a reviewer's report file), one row per
line:

    - <severity> <kind> <path[:line]>: <claim>

`severity` is `blocker`, `should-fix` or `nit`; `kind` is `follow-up` or `interaction`; blank lines are skipped. A
change to a file only the human edits is an `owner-doc` row, followed by a fenced `diff` block, its ready-to-apply
patch:

    - <severity> owner-doc <path>#<item>: <claim>
    ```diff
    <patch>
    ```

`<item>` names the part of the file the patch changes; a later `owner-doc` row for the same `<path>#<item>` replaces
the earlier one, whatever its ticket. The
section ends at the next `## ` heading or the first other line that is not a row (the report's block, `process:`
lines), so only a bullet that is not a row, or a first line that is not a row, is malformed. Each
row is recorded with ticket `TT`, PR `N` (null without `--pr`) and the report's path as its `source`, unless a row
with the same ticket, kind, file and claim is already there. Prints `OK TT recorded <n> new=<m>`: `n` rows in the
section, `m` of them new. A report with no section records 0.

`fixed` marks the rows with those ids (the `id` of each row in `followups.jsonl`) as fixed, recording the task whose
fix round fixed them (`true` without `--task`): a review finding sorted Fix is recorded as a row too, and a fixed row
is neither listed by `report` nor filed by `tracker_cmd.py file-followups`. Prints `OK run fixed <n>`.

`followups.py <run> report` prints the run's open follow-ups (fixed rows left out) for the final report, several stdout lines: `follow-ups: <n>`
(`follow-ups: 0` and nothing else for a run with none), then `<area>: <n> <severity>, ...` per area (a row's first path
segment), a blank line, the rows under `## <area>` and `### <severity>` headings, and `## Owner-only files`: per file
an `owner-doc` row or a guard `path` rule names (the repo's leave-for-human rules, read as `guard_rules.py` reads
them), its rows and one `diff` block joining its latest patch per item. Git's output goes to
`<run>/logs/run-followups.log`. It exits 0, or prints `FAIL run <reason>` and exits 1: `no-run-folder`, or the
reasons of `guard_rules.py` (`no-run-md`, `run-md-missing`, `git-fetch`, `guard-no-base`, `bad-rule`) when the guard
rules cannot be read; `FAIL run usage: ...` exits 2.

FAIL reasons:
- `usage: ...`: not `record <report> --ticket TT [--pr N]`, `fixed <id>... [--task <task>]` or `report`.
- `no-row: <id> ...`: no row has that id; nothing is marked.
- `bad-ticket: <value>`: `TT` is not a ticket number.
- `bad-pr: <value>`: `N` is not a PR number.
- `no-report: <path>`: the report file does not exist.
- `bad-followup <line>`: the section's first line, or a bullet in it, is not a row, or an `owner-doc` row has no
  `#<item>`, no non-empty `diff` block (closed by an unindented fence), or a patch whose `---`/`+++` headers name
  another path than the row's; nothing from that report is recorded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_rules  # noqa: E402
from steplib import core, followups  # noqa: E402

USAGE = "usage: followups.py <run> record <report> --ticket TT [--pr N] | fixed <id>... [--task <task>] | report"


def options(args):
    """`args` after the verb as (report, {flag: value}); None when they do not fit the usage."""
    if not args or args[0].startswith("--"):
        return None
    report, rest, opts = args[0], args[1:], {}
    while rest:
        if len(rest) < 2 or rest[0] not in ("--ticket", "--pr") or rest[0] in opts:
            return None
        opts[rest[0]] = rest[1]
        rest = rest[2:]
    return (report, opts) if "--ticket" in opts else None


def fixed(ctx):
    """`fixed <id>... [--task <task>]`: marks the rows `fixed`."""
    args = ctx.args[1:]
    task = True
    if "--task" in args:
        i = args.index("--task")
        if i != len(args) - 2 or not core.ID.match(args[-1]):
            raise core.Fail(USAGE)
        task, args = args[-1], args[:i]
    if not args or not all(a.isdigit() for a in args):
        raise core.Fail(USAGE)
    ids = [int(a) for a in args]
    have = {r["id"] for r in followups.load(ctx.run_dir)}  # rows are only ever added, so ids stay valid
    missing = [str(i) for i in ids if i not in have]
    if missing:
        raise core.Fail(f"no-row: {' '.join(missing)} (ids are those in followups.jsonl)")
    n = followups.mark(ctx.run_dir, ids, "fixed", task)
    return "fixed", str(n)


def step(ctx):
    args = ctx.args
    if args and args[0] == "fixed":
        return fixed(ctx)
    parsed = options(args[1:]) if args and args[0] == "record" else None
    if parsed is None:
        raise core.Fail(USAGE)
    report, opts = parsed
    ticket, pr = opts["--ticket"], opts.get("--pr")
    if not ticket.isdigit():
        raise core.Fail(f"bad-ticket: {ticket}")
    if pr is not None and not pr.isdigit():
        raise core.Fail(f"bad-pr: {pr}")
    path = core.resolve(report)
    if not path.is_file():
        raise core.Fail(f"no-report: {report}")
    try:
        rows = followups.parse(path.read_text(encoding="utf-8"))
    except followups.BadRow as e:
        raise core.Fail(f"bad-followup {e.line}")
    try:
        source = str(path.relative_to(ctx.run_dir))
    except ValueError:
        source = str(path)
    added = followups.add(ctx.run_dir, [{"ticket": ticket, "pr": int(pr) if pr else None, **row, "source": source}
                                        for row in rows])
    return "recorded", f"{len(rows)} new={len(added)}"


def argv_with_id(argv):
    """`<run> record <report> --ticket TT ...` with the ticket moved after `<run>`, as the id on every line; `run`
    when there is no `--ticket` value, so the usage FAIL names it."""
    rest = argv[1:]
    ident = "run"
    for i, word in enumerate(rest[:-1]):
        if word == "--ticket" and core.ID.match(rest[i + 1]):
            ident = rest[i + 1]
    return argv[:1] + [ident] + rest if argv else argv


def report(argv):
    """`<run> report`: prints the report's lines; returns the exit code."""
    if len(argv) != 2:
        print(f"FAIL run {USAGE}")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    rows = followups.open_rows(run)
    paths = []
    if rows:
        try:
            (run / "logs").mkdir(exist_ok=True)
            with open(run / "logs" / "run-followups.log", "a", encoding="utf-8") as log:
                paths = [r for r in guard_rules.rules(run, log) if r.kind == "path"]
        except core.Fail as e:
            print(f"FAIL run {e.reason}")
            return 1
    for line in followups.report(rows, lambda path: any(r.matches_path(path) for r in paths)):
        print(line)
    return 0


if __name__ == "__main__":
    if sys.argv[2:3] == ["report"]:
        sys.exit(report(sys.argv[1:]))
    core.main("followups", step, usage="record <report> --ticket TT [--pr N] | fixed <id>... [--task <task>]",
              argv=argv_with_id(sys.argv[1:]))
