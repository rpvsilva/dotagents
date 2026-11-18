#!/usr/bin/env python3
"""Records the run's rules in `<run>/decisions.md`, its only writer. Every step and prompt reads it through
`steplib/decisions.py`.

Usage:
  python3 -I decide.py <run> set <key> <value>
  python3 -I decide.py <run> clause "<clause>" yes|no|ask "<quote>"
  python3 -I decide.py <run> quote "<verbatim>"

`set` sets one header line (`base`, `ci` on|off, `merge_identity` app|human, `human`, `review_before_agent_merge`
yes|no, `test_command`, `remote` on|off (off for a run started with `--local`, before `setup.py`), `caps`, `parallel` (the run's slots, N), `red_base` accept (the human accepts a base whose suite fails, so setup
goes on) and `approved` yes, once the human approves the
plan) and prints `OK decisions set <key>`; an unknown key or value fails. `clause` adds the
Authorizations row for `<clause>`, or updates it, keeping the quote byte for byte, and prints
`OK decisions clause waived=<yes|no|ask>`. `quote` appends `<verbatim>` unchanged under Decisions, dated today, and
prints `OK decisions quote <date>`. A missing file is created.

`merge_identity:` and `human:` only record: every merge takes its identity from the `merge_identity` Setting (default
`app`), and a recorded `merge_identity:` other than the Setting refuses the merge (`wrong-identity`), never widens it.
The human's login comes from `gh api user` with the tokens unset; a recorded `human:` only narrows it. See
`steplib/identity.py`.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, decisions  # noqa: E402

USAGE = ('usage: decide.py <run> set <key> <value> | clause "<clause>" yes|no|ask "<quote>" | '
         'quote "<verbatim>"')


def step(ctx):
    args = ctx.args
    verb = args[0] if args else ""
    want = {"set": 3, "clause": 4, "quote": 2}.get(verb)
    if want is None or len(args) != want:
        raise core.Fail(USAGE)
    if verb == "set":
        decisions.check_value(args[1], args[2])
    elif verb == "clause":
        if args[2] not in decisions.WAIVED:
            raise core.Fail(f"bad-value: waived is one of {'|'.join(decisions.WAIVED)}")
        if any(c in s for s in (args[1], args[3]) for c in "\n\r"):
            raise core.Fail("newline: a clause and its quote are one line each")
        if not args[1].strip():
            raise core.Fail("empty-clause")
    elif "\r" in args[1]:
        raise core.Fail("carriage-return in quote")
    with decisions.lock(ctx.run_dir):
        d = decisions.load(ctx.run_dir) or decisions.Decisions()
        if verb == "set":
            d.headers[args[1]] = args[2]
            done = ("set", args[1])
        elif verb == "clause":
            row = decisions.Authorization(args[1], args[2], args[3])
            at = [i for i, a in enumerate(d.authorizations) if a.clause == row.clause]
            if at:
                d.authorizations[at[0]] = row
            else:
                d.authorizations.append(row)
            done = ("clause", f"waived={row.waived}")
        else:
            today = datetime.date.today().isoformat()
            d.decisions.append(decisions.Decision(today, args[1]))
            done = ("quote", today)
        decisions.save(ctx.run_dir, d)
    return done


if __name__ == "__main__":
    argv = sys.argv[1:]
    # The id on every line is `decisions`, so the log is `<run>/logs/decisions-decide.log`.
    core.main("decide", step, usage="set|clause|quote ...", argv=argv[:1] + ["decisions"] + argv[1:] if argv else argv)
