#!/usr/bin/env python3
"""The plan's open product questions and owner-gated test fixtures, for the approval message (step 5).

Usage: python3 -I plan_questions.py <run>

Reads `<run>/map.md` and the repo's leave-for-human guard rules, as `guard_rules.py` reads them (fetched from
`origin/<default>`, `Default:` in `run.md`). Prints, on stdout:
- `question: <field> x <command>`: one line per `?` cell of `map.md`'s `## Field matrix` table, row by row.
- `cost: <fixture path> rule <name>`: one line per path (a backticked word, a `:<line>` suffix dropped) in a line of
  `## Test isolation` that a guard `path` rule matches, per rule: the fixture lives in a file agents leave to the
  human, so the run needs that rule waived.
- `none` when it prints neither.
Like `permissions.py`, it prints several stdout lines; git's output goes to `<run>/logs/run-plan_questions.log`. Exits
0, or prints `FAIL run <reason>` and exits 1:
- `no-run-folder: <path>`: the run folder does not exist.
- `no-map-md`: the planner's pass B has not written `map.md`.
- `no-run-md`, `run-md-missing`, `git-fetch`, `guard-no-base`, `bad-rule`: the guard rules could not be read, as for
  `guard_rules.py`.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_rules  # noqa: E402
from steplib import core, runfolder  # noqa: E402

CITED = re.compile(r"`([^`\s]+)`")
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


def questions(map_text):
    body = runfolder.plan_check.section(map_text, "Field matrix") or ""
    return [f"question: {field} x {command}"
            for field, command, value in runfolder.plan_check.matrix_cells(body) if value == "?"]


def costs(map_text, rules):
    out = []
    paths = [r for r in rules if r.kind == "path"]
    for line in (runfolder.plan_check.section(map_text, "Test isolation") or "").splitlines():
        for word in CITED.findall(line):
            path = LINE_SUFFIX.sub("", word)
            out += [f"cost: {path} rule {r.name}" for r in paths if r.matches_path(path)]
    return list(dict.fromkeys(out))


def lines(run, log):
    path = run / "map.md"
    if not path.is_file():
        raise core.Fail("no-map-md: run the planner's pass B first")
    text = path.read_text(encoding="utf-8")
    return questions(text) + costs(text, guard_rules.rules(run, log)) or ["none"]


def main(argv):
    if len(argv) != 1:
        print("FAIL run usage: plan_questions.py <run>")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    try:
        (run / "logs").mkdir(exist_ok=True)
        with open(run / "logs" / "run-plan_questions.log", "a", encoding="utf-8") as log:
            out = lines(run, log)
    except core.Fail as e:
        print(f"FAIL run {e.reason}")
        return 1
    for line in out:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
