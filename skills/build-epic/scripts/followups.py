#!/usr/bin/env python3
"""Records the follow-ups a report file holds in `<run>/followups.jsonl`, through `steplib/followups.py`.

Usage:
  python3 -I followups.py <run> record <report> --ticket TT [--pr N]

`record` reads the `## Follow-ups` section of `<report>` (an implementer's or a reviewer's report file), one row per
line:

    - <severity> <kind> <path[:line]>: <claim>

`severity` is `blocker`, `should-fix` or `nit`; `kind` is `follow-up` or `interaction`; blank lines are skipped. The
section ends at the next `## ` heading or the first other line that is not a row (the report's block, `process:`
lines), so only a bullet that is not a row, or a first line that is not a row, is malformed. Each
row is recorded with ticket `TT`, PR `N` (null without `--pr`) and the report's path as its `source`, unless a row
with the same ticket, kind, file and claim is already there. Prints `OK TT recorded <n> new=<m>`: `n` rows in the
section, `m` of them new. A report with no section records 0.

FAIL reasons:
- `usage: ...`: not `record <report> --ticket TT [--pr N]`.
- `bad-ticket: <value>`: `TT` is not a ticket number.
- `bad-pr: <value>`: `N` is not a PR number.
- `no-report: <path>`: the report file does not exist.
- `bad-followup <line>`: the section's first line, or a bullet in it, is not a row; nothing from that report is
  recorded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, followups  # noqa: E402

USAGE = "usage: followups.py <run> record <report> --ticket TT [--pr N]"


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


def step(ctx):
    args = ctx.args
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


if __name__ == "__main__":
    core.main("followups", step, usage="record <report> --ticket TT [--pr N]", argv=argv_with_id(sys.argv[1:]))
